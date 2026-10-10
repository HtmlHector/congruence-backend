"""Standalone and embedded Socket.IO ASGI application."""

from __future__ import annotations

import socketio

from src.realtime.server import sio

# ASGI Application wrapper for Socket.IO
sio_app = socketio.ASGIApp(
    socketio_server=sio,
    socketio_path="socket.io",
)

__all__ = ["sio", "sio_app"]
