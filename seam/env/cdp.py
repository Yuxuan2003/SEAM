"""A dependency-free Chrome DevTools Protocol client.

RFC 6455 client framing over a plain socket, so the harness needs no
websocket library, playwright or selenium, and runs anywhere python3 does
(including against the Chrome inside the OSWorld guest). Only the handful of
CDP methods the harness needs are wrapped.

The local browser is $SEAM_CHROME if set, otherwise the first of
chrome-headless-shell / chromium / chromium-browser / google-chrome on PATH.
"""

from __future__ import annotations

import base64
import json
import os
import random
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

def _find_chrome() -> str:
    import shutil
    if os.environ.get("SEAM_CHROME"):
        return os.environ["SEAM_CHROME"]
    for c in ("chrome-headless-shell", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(c)
        if p:
            return p
    return "chromium"


CHROME = _find_chrome()


# --------------------------------------------------------------------------
# minimal websocket
# --------------------------------------------------------------------------

class WS:
    """Text-frame client. Handles 64-bit lengths and continuation frames,
    which matter because a 1280x800 screenshot arrives as ~700 KB of base64."""

    def __init__(self, url: str, timeout: float = 60.0):
        assert url.startswith("ws://"), url
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = self.sock.recv(4096)
            if not c:
                raise ConnectionError("handshake closed")
            buf += c
        head, _, tail = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode())
        self._rx = tail

    # ---- framing ----
    def _recv_exact(self, n: int) -> bytes:
        while len(self._rx) < n:
            c = self.sock.recv(max(4096, n - len(self._rx)))
            if not c:
                raise ConnectionError("closed mid-frame")
            self._rx += c
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        n = len(data)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, 0x80 | n)
        elif n < (1 << 16):
            hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
        mask = bytes(random.getrandbits(8) for _ in range(4))
        self.sock.sendall(hdr + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        chunks = []
        while True:
            b0, b1 = struct.unpack("!BB", self._recv_exact(2))
            op, fin, ln = b0 & 0x0F, b0 & 0x80, b1 & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", self._recv_exact(8))[0]
            if b1 & 0x80:                      # server must not mask
                self._recv_exact(4)
            payload = self._recv_exact(ln)
            if op == 0x8:
                raise ConnectionError("server closed")
            if op == 0x9:                      # ping -> pong
                self.sock.sendall(struct.pack("!BB", 0x8A, 0x80)
                                  + b"\x00\x00\x00\x00")
                continue
            if op == 0xA:
                continue
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# browser
# --------------------------------------------------------------------------

def die_with_parent() -> None:
    """preexec_fn: the child gets SIGTERM when the harness dies. A harness
    killed by `timeout` or a crash left 28 Chromium and 20 task servers orphaned
    on the login machine (found 2026-11-02, ~10 GB)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:
        pass


class Browser:
    def __init__(self, port: int, profile: Path, view: Tuple[int, int]):
        self.port, self.profile, self.view = port, profile, view
        self.proc: Optional[subprocess.Popen] = None
        self.ws: Optional[WS] = None
        self._id = 0

    @classmethod
    def launch(cls, url: str, port: int, profile: Path,
               view: Tuple[int, int] = (1280, 800), timeout: float = 90.0,
               extra_args: Tuple[str, ...] = ()) -> "Browser":
        b = cls(port, profile, view)
        profile.mkdir(parents=True, exist_ok=True)
        b.proc = subprocess.Popen(
            # No --default-background-color here: chrome-headless-shell treats
            # it as a "headless command" and then refuses remote debugging
            # outright ("Headless commands are not compatible with remote
            # debugging"), exiting before it ever opens the CDP port. The
            # viewport is set through Emulation below instead, which also
            # pins it to exactly the static renders' 1280x800.
            [str(CHROME), "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={view[0]},{view[1]}",
             f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
             *extra_args, url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=die_with_parent)
        t0 = time.time()
        wsurl = ""
        while time.time() - t0 < timeout:
            try:
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=3).read()
                for t in json.loads(raw):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        wsurl = t["webSocketDebuggerUrl"]
                        break
            except Exception:
                pass
            if wsurl:
                break
            time.sleep(0.4)
        if not wsurl:
            b.kill()
            raise TimeoutError(f"no CDP page target on :{port} after {timeout}s")
        b.ws = WS(wsurl)
        b.call("Page.enable")
        b.call("Runtime.enable")
        b.call("Emulation.setDeviceMetricsOverride", width=view[0], height=view[1],
               deviceScaleFactor=1, mobile=False)
        b.settle()
        return b

    def call(self, method: str, **params) -> Dict[str, Any]:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # events are ignored; the harness polls state instead

    # ---- the bits the harness uses ----
    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        self.settle()

    def url(self) -> str:
        return self.eval("location.pathname")

    def eval(self, js: str) -> Any:
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True)
        return r.get("result", {}).get("value")

    def settle(self, timeout: float = 10.0) -> bool:
        """Wait for readyState complete, then force one committed frame.

        The frame matters: mouse events are hit-tested by the compositor, not
        by the DOM, so the first click after a navigation is silently dropped
        if no frame has been composited yet -- `document.elementFromPoint`
        happily returns the right button while the click goes nowhere. It cost
        an hour on 2026-10-19: clicks on anchors appeared to work (they had a
        frame from the preceding interaction) while the submit button on a
        freshly navigated page did not. A screenshot is the cheapest way to
        make the compositor commit, and the harness takes one every step
        anyway."""
        t0 = time.time()
        ok = False
        while time.time() - t0 < timeout:
            try:
                if self.eval("document.readyState") == "complete":
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(0.15)
        if ok:
            try:
                self.call("Page.captureScreenshot", format="png")
            except Exception:
                pass
        return ok

    def screenshot(self) -> bytes:
        r = self.call("Page.captureScreenshot", format="png")
        return base64.b64decode(r["data"])

    def click(self, x: int, y: int) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=int(x), y=int(y),
                      button="left", clickCount=1, buttons=1 if kind == "mousePressed" else 0)
        self.settle()

    def boxes(self, selector: str = ".btn") -> Dict[int, Tuple[int, int, int, int]]:
        """Ground truth for scoring: index -> (x0,y0,x1,y1) in viewport pixels."""
        js = ("JSON.stringify(Array.prototype.map.call("
              f"document.querySelectorAll('{selector}'),"
              "function(e){var r=e.getBoundingClientRect();"
              "return [Math.round(r.left),Math.round(r.top),"
              "Math.round(r.right),Math.round(r.bottom)]}))")
        return {i: tuple(v) for i, v in enumerate(json.loads(self.eval(js) or "[]"))}

    def kill(self) -> None:
        if self.ws:
            self.ws.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.kill()
