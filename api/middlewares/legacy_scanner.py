"""Send an explicitly configured old scanner hostname to the unified workspace."""

import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse


class LegacyScannerRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        legacy_host = os.environ.get("SCANNER_LEGACY_HOST", "").strip().lower()
        canonical = os.environ.get("SCANNER_ORIGIN", "").strip().rstrip("/")
        origin = urlsplit(canonical)
        if not legacy_host or request.url.hostname != legacy_host:
            return await call_next(request)
        if origin.scheme not in ("http", "https") or not origin.netloc or origin.hostname == legacy_host:
            return JSONResponse({"error": "migration_origin_unavailable"}, status_code=503)
        # Never replay an old-site mutation or transmit its password to another endpoint.
        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"error": "workspace_moved", "location": canonical + "/scanner"}, status_code=409)
        path = request.url.path
        if path.startswith("/api/"):
            path = "/api/v1/scanner/" + path[len("/api/"):]
        elif not path.startswith("/scanner"):
            path = "/scanner" + (path if path != "/login" else "")
        query = "?" + request.url.query if request.url.query else ""
        return RedirectResponse(canonical + path + query, status_code=302)
