"""Local-only status endpoint for the optional in-Resolve HUD (manual 4.6).

Binds to 127.0.0.1 only. The HUD script inside Resolve is a thin client: it
reads the current step and can request Pause/Resume/Stop — no model code runs
in Resolve's process and scripting is never exposed on the network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vanta.workers.service import VantaService

ALLOWED_CONTROLS = {"pause", "resume", "stop", "step", "skip"}


class _State:
    service: VantaService | None = None
    last: dict = {}
    run_id: str | None = None


def _handler_factory():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            return

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/status"):
                state = _State.service.runs.get(_State.run_id) if _State.service and _State.run_id else None
                self._send(200, {"run_id": _State.run_id, "stage": state.stage if state else None, "progress": state.progress if state else None, "last": _State.last, "paused": state.control.paused if state else False, "approval": state.pending_approval if state else None})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            action = self.path.strip("/").split("/")[-1]
            if action not in ALLOWED_CONTROLS or not (_State.service and _State.run_id):
                self._send(400, {"error": "unsupported"})
                return
            try:
                self._send(200, _State.service.control(_State.run_id, action))
            except Exception as exc:
                self._send(409, {"error": str(exc)})

    return Handler


class HudServer:
    def __init__(self, service: VantaService, port: int):
        _State.service = service
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        service.subscribe(self._on_event)

    def _on_event(self, payload: dict) -> None:
        if payload.get("run_id") and payload["run_id"] != "run_library":
            _State.run_id = payload["run_id"]
        _State.last = {k: payload.get(k) for k in ("stage", "action", "status", "message", "confidence", "timestamp")}

    def start(self) -> bool:
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _handler_factory())
        except OSError:
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return True

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()


HUD_SCRIPT = r'''"""VANTA HUD — shows VANTA's current step inside DaVinci Resolve (Workspace > Scripts > VANTA HUD).
Thin client: it only polls VANTA's local endpoint and can send Pause/Resume/Stop."""
import json
import urllib.request

PORT = {port}
URL = "http://127.0.0.1:%d" % PORT

ui = fu.UIManager
disp = bmd.UIDispatcher(ui)
win = disp.AddWindow({{"ID": "VantaHUD", "WindowTitle": "VANTA", "Geometry": [60, 60, 420, 170]}}, [
    ui.VGroup([
        ui.Label({{"ID": "Stage", "Text": "VANTA: waiting", "Weight": 0, "Font": ui.Font({{"PixelSize": 15, "Bold": True}})}}),
        ui.Label({{"ID": "Msg", "Text": "", "WordWrap": True}}),
        ui.HGroup({{"Weight": 0}}, [ui.Button({{"ID": "Pause", "Text": "Pause"}}), ui.Button({{"ID": "Resume", "Text": "Resume"}}), ui.Button({{"ID": "Stop", "Text": "Stop"}})]),
    ])
])
items = win.GetItems()


def post(action):
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/control/" + action, data=b"{{}}", method="POST"), timeout=2)
    except Exception as exc:
        items["Msg"].Text = "VANTA not reachable: %s" % exc


def refresh(ev=None):
    try:
        data = json.loads(urllib.request.urlopen(URL + "/status", timeout=1).read().decode())
        last = data.get("last") or {{}}
        pct = data.get("progress")
        items["Stage"].Text = "VANTA - %s%s" % (last.get("stage") or "idle", "" if pct is None else "  %d%%" % (pct * 100))
        items["Msg"].Text = "%s  [%s]" % (last.get("message") or "", last.get("status") or "")
    except Exception:
        items["Stage"].Text = "VANTA app is not running"


win.On.Pause.Clicked = lambda ev: post("pause")
win.On.Resume.Clicked = lambda ev: post("resume")
win.On.Stop.Clicked = lambda ev: post("stop")
win.On.VantaHUD.Close = lambda ev: disp.ExitLoop()
timer = ui.Timer({{"ID": "Tick", "Interval": 700}})
disp.On.Tick.Timeout = refresh
timer.Start()
win.Show()
disp.RunLoop()
win.Hide()
'''


def install_hud_script(port: int) -> Path:
    import os

    appdata = os.environ.get("APPDATA", "")
    target = Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility" / "VANTA HUD.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(HUD_SCRIPT.format(port=port), encoding="utf-8")
    return target
