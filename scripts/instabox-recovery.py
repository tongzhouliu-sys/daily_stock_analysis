#!/usr/bin/env python3
"""Check a running DSA origin and serve a health-only Instabox wake port."""

import argparse
import fcntl
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(url):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=4) as response:
            return response.status, response.read(1024)
    except HTTPError as error:
        with error:
            return error.code, error.read(1024)
    except (OSError, URLError):
        return 0, b""


def app_ready(result):
    status, body = result
    if status != 200:
        return False
    try:
        return json.loads(body).get("status") == "ok"
    except (AttributeError, UnicodeError, ValueError):
        return False


def failed_component(local, public, tunnel):
    if not app_ready(local):
        return "web"
    if app_ready(public):
        return None
    code, body = public
    if (code == 530 and b"1033" in body) or (tunnel[0] != 200 and code in (0, 502, 503, 504, 530)):
        return "tunnel"
    return None


def check():
    deploy_dir = Path(os.environ["DSA_DEPLOY_DIR"])
    public_health = os.environ["DSA_PUBLIC_ORIGIN"].rstrip("/") + "/api/health"
    local_health = os.environ["DSA_LOCAL_HEALTH_URL"]
    tunnel_ready = os.environ["DSA_TUNNEL_READY_URL"]
    docker = ["docker", "--context", "default"]
    containers = {"web": os.environ["DSA_WEB_NAME"], "tunnel": os.environ["DSA_TUNNEL_NAME"]}

    with (deploy_dir / "deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if (deploy_dir / "pending-container").exists():
            return

        component = failed_component(probe(local_health), probe(public_health), probe(tunnel_ready))
        if component is None:
            return
        time.sleep(4)
        if failed_component(probe(local_health), probe(public_health), probe(tunnel_ready)) != component:
            return

        stamp = deploy_dir / f"recovery-{component}.stamp"
        now = time.time()
        if stamp.exists() and now - stamp.stat().st_mtime < 300:
            return
        container = containers[component]
        running = subprocess.run(
            [*docker, "inspect", "--format", "{{.State.Running}}", container],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if running.returncode or running.stdout.strip() != "true":
            return
        stamp.touch()
        stop_timeout = "8" if component == "tunnel" else "25"
        print(f"Restarting unhealthy {component} container {container}", flush=True)
        subprocess.run([*docker, "restart", "-t", stop_timeout, container], check=True, timeout=45)


def serve():
    public_origin = os.environ["DSA_PUBLIC_ORIGIN"].rstrip("/")
    local_health = os.environ["DSA_LOCAL_HEALTH_URL"]

    class WakeHandler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path not in ("/", "/ready"):
                self.send_response(404)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if app_ready(probe(local_health)):
                status = 303 if self.path == "/" else 200
                body = b"ready\n"
            else:
                status = 503
                body = b"Starting. Retry shortly.\n"
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if status == 303:
                self.send_header("Location", public_origin + "/")
            if status == 503:
                self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(body)

    address = os.environ["DSA_WAKE_BIND"], int(os.environ["DSA_WAKE_PORT"])
    with HTTPServer(address, WakeHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "serve"))
    os.umask(0o077)
    {"check": check, "serve": serve}[parser.parse_args().mode]()
