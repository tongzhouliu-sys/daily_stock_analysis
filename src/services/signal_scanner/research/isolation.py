"""Process audit guard, scoped to the executing thread/context (not a host firewall)."""

from src.services.signal_scanner.platform_support import descriptor_path
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from src.services.signal_scanner.core import DomainError

_guard = ContextVar("research_guard", default=None)


def _path(value):
    if isinstance(value, int):
        try:
            value = descriptor_path(value)
        except OSError:
            return None
    return Path(os.fsdecode(value)).absolute().resolve()


def _audit(event, args):
    state = _guard.get()
    if state is None:
        return
    if event in ("socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "socket.sendto"):
        state["network_attempts"] += 1
        raise DomainError("OFFLINE_NETWORK_FORBIDDEN")
    if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.exec", "pty.spawn"):
        state["subprocess_attempts"] += 1
        raise DomainError("OFFLINE_SUBPROCESS_FORBIDDEN")
    writing = False
    paths = []
    if event == "open":
        filename, mode, flags = args
        writing = bool(
            (isinstance(mode, str) and any(x in mode for x in "wax+"))
            or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        )
        paths = [filename]
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"):
        writing, paths = True, [args[0]]
    elif event in ("os.rename", "os.link", "os.symlink"):
        writing, paths = True, list(args[:2])
    elif event == "sqlite3.connect":
        writing, paths = True, [args[0]]
    if writing:
        for path in paths:
            resolved = _path(path)
            allowed = state["write_root"]
            if resolved is None or allowed is None or not resolved.is_relative_to(allowed):
                state["forbidden_writes"] += 1
                raise DomainError("RESEARCH_WRITE_FORBIDDEN")
        state["allowed_writes"] += 1


sys.addaudithook(_audit)


@contextmanager
def offline(write_root=None):
    state = dict(
        write_root=Path(write_root).resolve() if write_root else None,
        network_attempts=0,
        subprocess_attempts=0,
        forbidden_writes=0,
        allowed_writes=0,
    )
    token = _guard.set(state)
    try:
        yield state
    finally:
        _guard.reset(token)
