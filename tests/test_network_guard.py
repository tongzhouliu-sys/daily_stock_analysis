# -*- coding: utf-8 -*-
"""Unit tests for the optional offline network guard."""

from __future__ import annotations

import socket

import pytest

from tests.support import network_guard


def test_enforce_blocks_public_connect() -> None:
    token = network_guard.enable(enforce=True)
    try:
        with pytest.raises(RuntimeError, match="disabled in offline tests"):
            socket.create_connection(("93.184.216.34", 80), timeout=1)
    finally:
        network_guard.disable(token)


def test_enforce_blocks_public_dns() -> None:
    token = network_guard.enable(enforce=True)
    try:
        with pytest.raises(RuntimeError, match="DNS resolution is disabled"):
            socket.getaddrinfo("example.com", 443)
    finally:
        network_guard.disable(token)


def test_loopback_is_allowed() -> None:
    token = network_guard.enable(enforce=True)
    try:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 1), timeout=0.2)
    finally:
        network_guard.disable(token)


def test_warn_mode_does_not_raise() -> None:
    token = network_guard.enable(enforce=False)
    try:
        network_guard._audit("socket.connect", (object(), ("93.184.216.34", 80)))
    finally:
        network_guard.disable(token)
