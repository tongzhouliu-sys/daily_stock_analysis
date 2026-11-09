"""The offline suite must exercise fallback without contacting live providers."""

import socket

import pytest


@pytest.fixture(autouse=True)
def prevent_unmarked_network(request, monkeypatch):
    if request.node.get_closest_marker('network'):
        return

    def unavailable(*args, **kwargs):
        raise OSError('Network disabled for deterministic market-router tests')

    monkeypatch.setattr(socket.socket, 'connect', unavailable)
    monkeypatch.setattr(socket, 'create_connection', unavailable)
    monkeypatch.setattr(socket, 'getaddrinfo', unavailable)
    # The optional SDK retries transport failures with exponential sleeps.
    # Fail its transport once while still exercising the adapter and fallback.
    try:
        from zzshare.core import BaseDataApi
    except ImportError:
        pass
    else:
        monkeypatch.setattr(BaseDataApi, '_request_with_retry', unavailable)
    try:
        from ashareapi import AShareAPI
    except ImportError:
        pass
    else:
        monkeypatch.setattr(AShareAPI, '_request', unavailable)
