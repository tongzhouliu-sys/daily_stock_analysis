# -*- coding: utf-8 -*-
"""Offline network guard for the default pytest suite.

Tests marked ``network`` (or run with ``DSA_ALLOW_NETWORK=1``) are exempt.
Everything else must stay inside the machine: loopback, private/LAN and unix
sockets are allowed; public addresses raise immediately.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from contextvars import ContextVar
from typing import Any, Optional

_ENABLED: ContextVar[bool] = ContextVar("dsa_offline_network_guard", default=False)
_ENFORCE: ContextVar[bool] = ContextVar("dsa_offline_network_guard_enforce", default=True)
_INSTALLED = False
_ENV_FLAG = "DSA_OFFLINE_GUARD"
_WARNED: set[str] = set()


def _active() -> bool:
    return _ENABLED.get() or os.getenv(_ENV_FLAG) in {"1", "warn", "enforce"}


def _enforcing() -> bool:
    return _ENFORCE.get() and os.getenv(_ENV_FLAG) != "warn"


def _is_local_host(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    text = str(host).strip()
    if not text:
        return True
    if text.startswith("/"):
        # AF_UNIX path.
        return True
    if text in {"localhost", "testclient", "0.0.0.0", "::"}:
        return True
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        # Hostnames are resolved before connect(); the resolved address is what
        # this guard sees on the socket.connect event.
        return True
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


def _is_local_literal(host: Any) -> bool:
    """Return True only for loopback/private literals and known local names."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    text = str(host).strip()
    if not text:
        return True
    if text in {"localhost", "testclient", "0.0.0.0", "::"}:
        return True
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


def _address_host(address: Any) -> Any:
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


def _blocked(message: str) -> None:
    if _enforcing():
        raise RuntimeError(message)
    if message in _WARNED:
        return
    _WARNED.add(message)
    import logging

    logging.getLogger(__name__).warning(message)


def _audit(event: str, args: tuple) -> None:
    if not _active() or os.getenv("DSA_ALLOW_NETWORK") == "1":
        return
    if event in {"socket.connect", "socket.connect_ex"}:
        host = _address_host(args[1] if len(args) > 1 else None)
        if not _is_local_host(host):
            _blocked(
                f"outbound network is disabled in offline tests (target={host!r}); "
                "mark the test with @pytest.mark.network or set DSA_ALLOW_NETWORK=1"
            )
    elif event == "socket.getaddrinfo":
        host = args[0] if args else None
        if host is None:
            return
        text = str(host)
        if _is_local_literal(text):
            return
        # A non-literal hostname cannot be proven local without resolving it,
        # and resolving it is exactly what may hang the offline suite.
        _blocked(
            f"DNS resolution is disabled in offline tests (host={text!r}); "
            "mark the test with @pytest.mark.network or set DSA_ALLOW_NETWORK=1"
        )
    elif event == "socket.sendto":
        host = _address_host(args[1] if len(args) > 1 else None)
        if not _is_local_host(host):
            _blocked(
                f"outbound network is disabled in offline tests (target={host!r})"
            )


def install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_audit)
        _INSTALLED = True


def enable(enforce: bool = True) -> Any:
    install()
    previous = os.environ.get(_ENV_FLAG)
    os.environ[_ENV_FLAG] = "enforce" if enforce else "warn"
    return (_ENABLED.set(True), _ENFORCE.set(enforce), previous)


def disable(token: Any) -> None:
    context_token, enforce_token, previous = token
    _ENABLED.reset(context_token)
    _ENFORCE.reset(enforce_token)
    if previous is None:
        os.environ.pop(_ENV_FLAG, None)
    else:
        os.environ[_ENV_FLAG] = previous


def is_enabled() -> bool:
    return _ENABLED.get()


def is_exempt(node: Any) -> bool:
    if os.getenv("DSA_ALLOW_NETWORK") == "1":
        return True
    getter = getattr(node, "get_closest_marker", None)
    if getter is None:
        return False
    return getter("network") is not None


def blocked_reason(host: Optional[Any]) -> Optional[str]:
    if is_enabled() and not _is_local_host(host):
        return f"outbound network is disabled in offline tests (target={host!r})"
    return None
