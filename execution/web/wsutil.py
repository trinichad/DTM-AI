"""Minimal WebSocket (RFC 6455) for the stdlib http.server — no external deps.

Just enough to bridge an interactive PTY (see pty_session): server handshake + text/binary frame
read/write over the raw connection. The request handler detects the Upgrade in do_GET and hands the
socket here; after the 101 it must never return to the normal HTTP response path. Stdlib only.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Tuple

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def accept_key(key: str) -> str:
    """RFC 6455 Sec-WebSocket-Accept value for a given client key."""
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


def is_ws_upgrade(headers) -> bool:
    return (headers.get("Upgrade", "").lower() == "websocket"
            and "upgrade" in headers.get("Connection", "").lower())


def handshake(handler) -> bool:
    """Complete the server handshake on a BaseHTTPRequestHandler. Returns True on success."""
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key:
        return False
    resp = ("HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n")
    handler.wfile.write(resp.encode())
    handler.wfile.flush()
    return True


def encode(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    """Server→client frame: unmasked, single, FIN=1."""
    header = bytes([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    return header + payload


def encode_text(s: str) -> bytes:
    return encode(s.encode("utf-8"), OP_TEXT)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def read_frame(sock: socket.socket) -> Tuple[int, bytes]:
    """Read one client message → (opcode, payload). Unmasks, reassembles fragments. Control frames
    (close/ping/pong) return immediately. Raises ConnectionError/OSError on EOF."""
    payload = b""
    first_op = None
    while True:
        b0, b1 = _recv_exact(sock, 2)
        fin = b0 & 0x80
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
        data = _recv_exact(sock, ln) if ln else b""
        if masked:
            data = bytes(data[i] ^ mask[i % 4] for i in range(len(data)))
        if opcode in (OP_CLOSE, OP_PING, OP_PONG):
            return opcode, data
        if first_op is None:
            first_op = opcode if opcode != OP_CONT else OP_TEXT
        payload += data
        if fin:
            return first_op, payload
