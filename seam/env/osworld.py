"""SEAM tasks in the OSWorld Ubuntu guest, through OSWorld's own DesktopEnv.

This is the measurement environment of the paper: the guest's own Chrome,
real pyautogui clicks at screen pixels, the task site served inside the guest
on port 80 under its own host names, and the outcome read from the guest's
events.jsonl. Each task file (data/suite/tasks/<id>.json, built by
`seam.build`) is a standard OSWorld task config: its setup uploads the task's
site bundle, starts the site, launches Chrome with remote debugging behind
socat (OSWorld's usual chromium setup), and opens the inbox.

What this layer adds on top of DesktopEnv:

  click     scroll the element into view, translate its viewport box to screen
            pixels with the window's own offsets, pyautogui.click in the guest
  type      a real click to focus, then CDP Input.insertText (real input
            events; pyautogui.write cannot type "—" or "°")
  scroll    pyautogui.scroll over the page
  shot      the whole 1920x1080 screen, Chrome's frame and address bar included
  url/marks read from the guest Chrome over CDP (DesktopEnv's chromium port)
select / go_back / open_url stay DOM/CDP-level (a native <select> popup is
not reliably drivable by pixels; OSWorld SoM agents do the same).

Requires OSWorld (https://github.com/xlang-ai/OSWorld) importable as
`desktop_env`; the paper used commit b138d348 with its stock Ubuntu image.

    python -m seam.agent --model <name> --suite data/suite --env osworld \\
        --osworld-provider docker --workers 4 --out runs/x.jsonl
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import cdp
from .. import agent as AG
from .. import build as BLD
from ..server import whoami

SCREEN = (1920, 1080)
#: typing in the guest: xdotool with real key events (falls back to pyautogui
#: for ASCII if xdotool is missing)
TYPE_PY = ("import subprocess, pyautogui\n"
           "t = {text}\n"
           "r = subprocess.run(['xdotool', 'type', '--delay', '8', '--', t], capture_output=True)\n"
           "if r.returncode != 0:\n"
           "    pyautogui.write(t, interval=0.01)\n")
WINDOW_JS = ("JSON.stringify([window.screenX, window.screenY, window.outerWidth,"
             " window.outerHeight, window.innerWidth, window.innerHeight,"
             " window.devicePixelRatio])")


def page_to_screen(box: List[float], win: List[float]) -> List[int]:
    """Viewport CSS box -> screen pixel box.

    Chrome on Linux draws its tab strip and toolbar above the viewport and no
    side border, so the viewport origin is (screenX + side, screenY + top) with
    side = (outerWidth - innerWidth) / 2 and top = outerHeight - innerHeight -
    side. devicePixelRatio scales CSS pixels to screen pixels."""
    sx, sy, ow, oh, iw, ih, dpr = win
    dpr = dpr or 1.0
    side = max(0.0, (ow - iw) / 2.0)
    top = max(0.0, oh - ih - side)
    ox, oy = sx + side, sy + top
    x0, y0, x1, y1 = box
    return [int(round(ox + x0 * dpr)), int(round(oy + y0 * dpr)),
            int(round(ox + x1 * dpr)), int(round(oy + y1 * dpr))]


def osworld_config(task: Dict, suite: Path) -> Dict:
    """The task's OSWorld config with the bundle path resolved to `suite`."""
    cfg = copy.deepcopy(BLD.osworld_task(task))
    for step in cfg["config"]:
        if step["type"] == "upload_file":
            for f in step["parameters"]["files"]:
                f["local_path"] = f["local_path"].replace("<bundles>", str(suite))
    return cfg


class OSWorldEnv:
    """The interface `seam.agent.loop` drives, over one OSWorld DesktopEnv."""

    def __init__(self, suite: Path, provider: str = "docker", **desktop_env_kwargs):
        from desktop_env.desktop_env import DesktopEnv      # OSWorld
        self.suite = Path(suite)
        kw = dict(provider_name=provider, action_space="pyautogui", screen_size=SCREEN,
                  require_a11y_tree=False, os_type="Ubuntu", headless=True)
        kw.update(desktop_env_kwargs)
        self.env = DesktopEnv(**kw)
        self.task: Dict = {}
        self._pixel = False          # set once a pixel protocol drives this env
        self._ws: Optional[cdp.WS] = None
        self._id = 0

    # ---- lifecycle ----
    def load(self, task: Dict) -> Dict:
        self.task, self._pixel = task, False
        self._close_ws()
        self.env.reset(task_config=osworld_config(task, self.suite))
        size = tuple(self.env.vm_screen_size() or ())
        if size and tuple(int(v) for v in size[:2]) != SCREEN:
            raise RuntimeError(f"guest screen is {size}, expected {SCREEN}")
        self._attach(task["mail"]["host"])
        # the site in the guest must be THIS task's (the token, not the id)
        tok = self._fetch_text(f"http://{task['mail']['host']}/whoami")
        if tok != whoami(task["id"]):
            raise RuntimeError(f"guest site answers {tok!r}, not this task")
        return {"screen": SCREEN}

    def close(self) -> None:
        self._close_ws()
        self.env.close()

    # ---- CDP into the guest Chrome through DesktopEnv's chromium port ----
    def _cdp_base(self) -> str:
        return f"{self.env.vm_ip}:{self.env.chromium_port}"

    def _tabs(self) -> List[Dict]:
        return [t for t in json.loads(urllib.request.urlopen(
            f"http://{self._cdp_base()}/json/list", timeout=3).read()) if t.get("type") == "page"]

    def _ws_url(self, t: Dict) -> str:
        # the guest reports its own address in the websocket URL; route it
        # through the forwarded port instead
        return re.sub(r"^ws://[^/]+", f"ws://{self._cdp_base()}", t.get("webSocketDebuggerUrl") or "")

    def _attach(self, host: str, timeout: float = 60.0) -> None:
        t0, url = time.time(), ""
        while time.time() - t0 < timeout and not url:
            try:
                url = next((self._ws_url(t) for t in self._tabs() if host in (t.get("url") or "")), "")
            except Exception:                                 # noqa: BLE001
                pass
            if not url:
                time.sleep(0.5)
        if not url:
            raise RuntimeError(f"no guest Chrome tab on {host} via {self._cdp_base()}")
        self._ws = cdp.WS(url)
        self._call("Runtime.enable")
        self._call("Page.bringToFront")

    def _close_ws(self) -> None:
        if self._ws:
            self._ws.close()
            self._ws = None

    def _call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self._ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def _eval(self, js: str):
        r = self._call("Runtime.evaluate", expression=js, returnByValue=True)
        return r.get("result", {}).get("value")

    def _settle(self, timeout: float = 10.0) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self._eval("document.readyState") == "complete":
                    return
            except Exception:                                 # noqa: BLE001
                pass
            time.sleep(0.2)

    def window(self) -> List[float]:
        return json.loads(self._eval(WINDOW_JS))

    def _fetch_text(self, url: str) -> str:
        r = self._call("Runtime.evaluate", expression=(
            f"fetch({json.dumps(url)}).then(function(r){{return r.text()}})"),
            awaitPromise=True, returnByValue=True)
        return (r.get("result") or {}).get("value") or ""

    # ---- guest commands ----
    def _py(self, code: str) -> None:
        res = self.env.controller.execute_python_command(code)
        if not res or res.get("status") not in (None, "success") or (res.get("returncode") or 0) != 0:
            raise RuntimeError(f"guest exec failed: {str(res)[:300]}")

    def _click(self, x: int, y: int) -> None:
        self._py(f"import pyautogui; pyautogui.click({int(x)}, {int(y)})")
        # a POST/navigation may detach the old document; re-settle
        time.sleep(0.5)
        self._settle()

    # ---- loop interface ----
    def url(self) -> str:
        if self._pixel:
            return self._js("location.href", "") or ""
        return self._eval("location.href") or ""

    def marks(self, draw: bool = True) -> List:
        if not draw:                 # only the pixel protocols ask for no tags
            self._pixel = True
            return json.loads(self._js(AG.tag_js(draw), "[]") or "[]")
        return json.loads(self._eval(AG.tag_js(draw)) or "[]")

    def _js(self, js: str, default):
        """A page read for a pixel agent, which can close the tab we read or
        Chrome itself or leave a tab hung. That is the agent's doing, so the
        episode goes on with what the screen shows: try the visible tab once
        more, else report nothing."""
        for attempt in (0, 1):
            try:
                return self._eval(js)
            except (OSError, RuntimeError, ValueError, AttributeError):
                if attempt:
                    return default
                self._follow_visible_tab(force=True)

    def shot(self) -> bytes:
        png = self.env.controller.get_screenshot()
        if not png:
            raise RuntimeError("guest screenshot failed")
        return png

    def read_events(self) -> List[Dict]:
        raw = self.env.controller.get_file(f"{BLD.GUEST}/events.jsonl")
        out = []
        for x in (raw or b"").decode("utf-8", "replace").splitlines():
            if x.strip():
                try:
                    out.append(json.loads(x))
                except ValueError:
                    pass
        return out

    def _el(self, n) -> str:
        return f"document.querySelector('[data-som=\"{int(n)}\"]')"

    def _centre(self, n) -> Optional[List[int]]:
        box = self._eval(
            f"(function(e){{if(!e)return null;e.scrollIntoView({{block:'center'}});"
            f"var r=e.getBoundingClientRect();return JSON.stringify([r.left,r.top,r.right,r.bottom])}})"
            f"({self._el(n)})")
        if not box:
            return None
        time.sleep(0.2)
        b = page_to_screen(json.loads(box), self.window())
        return [(b[0] + b[2]) // 2, (b[1] + b[3]) // 2]

    def _at(self, p) -> Optional[List[int]]:
        """A point the model gave, in guest screen pixels (both pixel
        protocols are absolute on the 1920x1080 screenshot)."""
        try:
            x, y = int(p[0]), int(p[1])
        except (TypeError, ValueError, IndexError):
            return None
        w, h = SCREEN
        return [x, y] if 0 <= x < w and 0 <= y < h else None

    def act(self, a: Dict) -> str:
        t = a.get("type")
        # pixel protocols (UI-TARS, OpenCUA): a point, not an element; a pixel
        # `type` has no point and goes to whatever has the focus
        if "point" in a or t in ("hotkey", "drag", "wait") or (t == "type" and "element" not in a):
            return self._act_pixel(a)
        if t in ("click", "type"):
            xy = self._centre(a.get("element", -1))
            if xy is None:
                return f"There is no element {a.get('element')} on this page. "
            if t == "type":
                ok = self._eval(f"(function(e){{return !!e && ('value' in e)}})({self._el(a['element'])})")
                if not ok:
                    return f"Element {a.get('element')} is not a text field. "
            self._click(*xy)
            if t == "type":
                self._eval(f"(function(e){{e.focus();e.value=''}})({self._el(a['element'])})")
                self._call("Input.insertText", text=str(a.get("text") or ""))
        elif t == "select":
            opt = json.dumps(a.get("option") or "")
            ok = self._eval(f"(function(e){{if(!e||e.tagName!=='SELECT')return false;for(var i=0;i<e.options.length;i++){{if(e.options[i].text.trim()=={opt}.trim()){{e.selectedIndex=i;e.dispatchEvent(new Event('change',{{bubbles:true}}));return true}}}}return false}})({self._el(a.get('element', -1))})")
            if not ok:
                return f"Could not select that option in element {a.get('element')}. "
        elif t == "scroll":
            sx, sy, ow, oh, iw, ih, _ = self.window()
            self._py(f"import pyautogui; pyautogui.moveTo({int(sx + ow / 2)}, {int(sy + oh / 2)}); "
                     f"pyautogui.scroll({-6 if a.get('direction') != 'up' else 6})")
        elif t == "go_back":
            self._eval("history.back()")
        elif t == "open_url":
            self._call("Page.navigate", url=str(a.get("url") or ""))
        time.sleep(0.6)
        self._settle()
        return ""

    def _act_pixel(self, a: Dict) -> str:
        t = a.get("type")
        if t == "click":
            xy = self._at(a.get("point"))
            if xy is None:
                return f"{a.get('point')} is not a point on the {SCREEN[0]}x{SCREEN[1]} screen. "
            btn = "right" if a.get("button") == "right" else "left"
            n = 2 if int(a.get("clicks") or 1) > 1 else 1
            self._py(f"import pyautogui; pyautogui.click({xy[0]}, {xy[1]}, "
                     f"clicks={n}, interval=0.12, button={btn!r})")
        elif t == "type":
            xy = self._at(a.get("point"))
            if xy is not None:
                self._click(*xy)
            txt = str(a.get("text") or "")
            if txt:
                # real keystrokes to whatever has the X focus: a pixel agent
                # types where it clicked (address bar, a new tab, a field)
                self._py(TYPE_PY.format(text=json.dumps(txt)))
            if a.get("submit"):
                self._py("import pyautogui; pyautogui.press('enter')")
        elif t == "scroll":
            xy = self._at(a.get("point")) or [SCREEN[0] // 2, SCREEN[1] // 2]
            self._py(f"import pyautogui; pyautogui.moveTo({xy[0]}, {xy[1]}); "
                     f"pyautogui.scroll({-6 if a.get('direction') != 'up' else 6})")
        elif t == "drag":
            p, q = self._at(a.get("point")), self._at(a.get("to"))
            if p is None or q is None:
                return "A drag needs two points on the screen. "
            self._py(f"import pyautogui; pyautogui.moveTo({p[0]}, {p[1]}); "
                     f"pyautogui.dragTo({q[0]}, {q[1]}, duration=0.4)")
        elif t == "hotkey":
            keys = [str(k) for k in (a.get("keys") or []) if str(k).strip()]
            if not keys:
                return "That hotkey had no keys. "
            self._py(f"import pyautogui; pyautogui.hotkey({', '.join(repr(k) for k in keys[:4])})")
        elif t == "go_back":
            self._eval("history.back()")
        elif t == "wait":
            time.sleep(3)
        time.sleep(0.6)
        self._follow_visible_tab()
        self._settle()
        return ""

    def _follow_visible_tab(self, force: bool = False) -> None:
        """Keep CDP (URL, element boxes, page reads) on the tab the screen
        shows: a pixel agent can open tabs, and the screenshot then shows a
        different page from the one we were attached to."""
        try:
            tabs = self._tabs()
        except Exception:                                     # noqa: BLE001
            return
        if len(tabs) < 2 and not force and tabs and self._alive():
            return
        for t in tabs:
            url = self._ws_url(t)
            if not url:
                continue
            try:
                ws = cdp.WS(url, timeout=15)
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": "document.visibilityState",
                                               "returnByValue": True}}))
                while True:
                    m = json.loads(ws.recv())
                    if m.get("id") == 1:
                        break
                vis = ((m.get("result") or {}).get("result") or {}).get("value")
            except Exception:                                 # noqa: BLE001
                continue
            if vis == "visible":
                self._close_ws()
                self._ws = ws
                self._call("Runtime.enable")
                return
            ws.close()

    def _alive(self) -> bool:
        try:
            return self._eval("1") == 1
        except Exception:                                     # noqa: BLE001
            return False


def episode_factory(suite: Path, provider: str = "docker", **kw) -> Callable[[Path, Dict], Dict]:
    """A callable (task_dir, cfg) -> record for `seam.agent.episode`, keeping
    one DesktopEnv per worker thread (a VM is reused across that worker's
    episodes; DesktopEnv.reset reverts it to the snapshot)."""
    local = threading.local()

    def run(task_dir: Path, cfg: Dict) -> Dict:
        if getattr(local, "env", None) is None:
            local.env = OSWorldEnv(suite, provider=provider, **kw)
        task = json.loads((Path(task_dir) / "task.json").read_text())
        try:
            local.env.load(task)
            return AG.loop(local.env, task, cfg)
        except Exception:
            try:
                local.env.close()
            finally:
                local.env = None
            raise

    return run
