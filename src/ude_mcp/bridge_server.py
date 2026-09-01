"""Local HTTP bridge that the in-UDE JScript agent talks to.

The agent (assets/bridge.js) long-polls /next for commands and posts
results to /result. MCP tools enqueue commands here and block on an
event until the agent answers (or a timeout elapses).
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LONGPOLL_SECONDS = 30.0


class BridgeServer:
    """Single-session command broker between MCP tools and the UDE agent."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._requested_port = port
        self._ids = itertools.count(1)

        self._cond = threading.Condition()
        self._queued: dict | None = None          # command waiting for pickup
        self._awaited_id: int | None = None       # id the tools are waiting on
        self._result: dict | None = None
        self._result_event = threading.Event()

        self._last_status: dict = {}
        self._agent_seen = threading.Event()
        self._call_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("bridge not started")
        return self._server.server_address[1]

    def start(self) -> None:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence default access log
                pass

            def _send(self, obj: dict, code: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    self._send({"error": "bad json"}, 400)
                    return

                if self.path == "/next":
                    bridge._on_poll(payload)
                    cmd = bridge._take_command(_LONGPOLL_SECONDS)
                    if cmd is None:
                        self._send({"cmd": "idle"})
                    else:
                        self._send(cmd)
                    return

                if self.path == "/result":
                    bridge._on_result(payload)
                    self._send({"ok": True})
                    return

                self._send({"error": "unknown path"}, 404)

        self._server = ThreadingHTTPServer((self._host, self._requested_port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="ude-mcp-bridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- agent side (HTTP thread) ------------------------------------------

    def _on_poll(self, payload: dict) -> None:
        self._last_status = payload.get("status") or {}
        self._agent_seen.set()

    def _take_command(self, wait: float) -> dict | None:
        deadline = time.monotonic() + wait
        with self._cond:
            while True:
                if self._queued is not None:
                    cmd = self._queued
                    self._queued = None
                    self._awaited_id = cmd.get("id")
                    return cmd
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _on_result(self, payload: dict) -> None:
        with self._cond:
            self._result = payload
        self._result_event.set()

    # -- tool side -----------------------------------------------------------

    @property
    def last_status(self) -> dict:
        return self._last_status

    def wait_for_agent(self, timeout: float) -> bool:
        return self._agent_seen.wait(timeout)

    def call(self, cmd: str, bridge_timeout: float = 30.0, /, **args):
        """Run one command on the agent and wait for its result value.

        ``bridge_timeout`` bounds the wait on the MCP side; all other
        keyword arguments are forwarded to the agent command (so commands
        may use their own ``timeout`` argument name).
        """
        with self._call_lock:
            return self._call_locked(cmd, bridge_timeout, args)

    def _call_locked(self, cmd: str, timeout: float, args: dict):
        call_id = next(self._ids)
        with self._cond:
            self._queued = {"id": call_id, "cmd": cmd, "args": args}
            self._result = None
            self._awaited_id = None
            self._result_event.clear()
            self._cond.notify_all()

        if not self._result_event.wait(timeout):
            with self._cond:
                if self._awaited_id != call_id:
                    self._queued = None  # cancel while still queued
            raise TimeoutError(f"UDE agent did not answer '{cmd}' within {timeout}s")

        result = self._result or {}
        if result.get("id") != call_id:
            raise RuntimeError("bridge: result id mismatch")
        if not result.get("ok"):
            raise RuntimeError(f"UDE error: {result.get('error')}")
        return result.get("value")
