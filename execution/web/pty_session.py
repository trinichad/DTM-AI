"""Interactive PTY bridged to a WebSocket — the engine behind the dashboard Terminal (D-22).

Ported from the RossMeta AI design (its execution/terminal.py), adapted from asyncio to threads + select
so it fits DTM AI's stdlib ThreadingHTTPServer (one thread per connection). Forks a real login shell on a
pseudo-terminal so the browser can host full TUIs — vim, top, interactive `claude`, password prompts,
colors. Runs as whatever user the server process is (dtm-ai on :8090; root on :8091). Stdlib only.

Wire protocol (JSON text frames, matches RossMeta):
  client→server  {"type":"in","data":"..."}            keystrokes
                 {"type":"resize","cols":N,"rows":N}    terminal size
  server→client  {"type":"out","data":"..."}            shell output
                 {"type":"exit"}                          shell ended
"""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import struct
import termios
from pathlib import Path

from . import wsutil

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHELL = os.environ.get("SHELL", "/bin/bash")
_PATH = os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", "")


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _send(sock, data: bytes) -> bool:
    try:
        sock.sendall(data)
        return True
    except OSError:
        return False


def serve(sock, cwd: str = None) -> None:
    """Fork a login shell on a PTY and bridge it to the already-upgraded WebSocket `sock`. Blocks until
    the shell exits or the socket closes; always reaps the child (no orphan shells). Does NOT close
    `sock` — the HTTP handler owns its teardown."""
    cwd = cwd or str(_PROJECT_ROOT)
    pid, fd = pty.fork()
    if pid == 0:  # child → become the login shell
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        os.environ.setdefault("LANG", "C.UTF-8")
        os.environ.setdefault("LC_ALL", "C.UTF-8")
        os.environ["PATH"] = _PATH
        os.environ["DTM_TERMINAL"] = "1"
        try:
            os.chdir(cwd)
        except OSError:
            pass
        os.execvp(SHELL, [SHELL, "-l"])
        os._exit(1)

    # parent → bridge fd ⟷ sock
    try:
        try:
            _set_winsize(fd, 30, 100)   # sane size before the browser's first resize
        except OSError:
            pass
        sock.setblocking(True)
        while True:
            try:
                r, _, _ = select.select([fd, sock], [], [])
            except (OSError, ValueError):
                break
            if fd in r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""
                if not data:  # shell exited
                    _send(sock, wsutil.encode_text(json.dumps({"type": "exit"})))
                    break
                if not _send(sock, wsutil.encode_text(json.dumps(
                        {"type": "out", "data": data.decode("utf-8", "replace")}))):
                    break
            if sock in r:
                try:
                    opcode, raw = wsutil.read_frame(sock)
                except (ConnectionError, OSError):
                    break
                if opcode == wsutil.OP_CLOSE:
                    break
                if opcode == wsutil.OP_PING:
                    _send(sock, wsutil.encode(raw, wsutil.OP_PONG))
                    continue
                if opcode not in (wsutil.OP_TEXT, wsutil.OP_BIN):
                    continue
                try:
                    m = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if m.get("type") == "in":
                    try:
                        os.write(fd, (m.get("data") or "").encode())
                    except OSError:
                        break
                elif m.get("type") == "resize":
                    try:
                        _set_winsize(fd, int(m.get("rows", 24)), int(m.get("cols", 80)))
                    except (OSError, ValueError):
                        pass
    finally:
        for cleanup in (lambda: os.kill(pid, signal.SIGKILL),
                        lambda: os.waitpid(pid, 0),
                        lambda: os.close(fd)):
            try:
                cleanup()
            except OSError:
                pass
