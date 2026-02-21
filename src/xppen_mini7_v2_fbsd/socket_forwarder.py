from __future__ import annotations

import json
import logging
import os
import socket
from typing import Optional

from .stylus import StylusSample

LOG = logging.getLogger(__name__)


class SocketForwarder:
    def __init__(self, path: str) -> None:
        self.path = path
        self.sock: Optional[socket.socket] = None
        self._connect()

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise RuntimeError(f"Could not connect to {self.path}: {exc}")
        self.sock = sock

    def forward(self, sample: StylusSample) -> None:
        if not self.sock:
            return
        payload = json.dumps(sample.__dict__).encode() + b"\n"
        try:
            self.sock.sendall(payload)
        except OSError as exc:
            LOG.error("Socket send failed: %s", exc)
            self.sock.close()
            self.sock = None

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None
