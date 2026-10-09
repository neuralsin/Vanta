"""Flight recorder (manual 4.1 D / 4.7): screen-record the Resolve window during a run.

Uses FFmpeg gdigrab on the window titled 'DaVinci Resolve' (falls back to the
desktop). The recording is indexed by run events (timestamps) for replay.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from vanta.analysis.media import _tool


class FlightRecorder:
    def __init__(self, out_path: Path, *, fps: int = 15, window_title: str = "DaVinci Resolve"):
        self.out_path = out_path
        self.fps = fps
        self.window_title = window_title
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None

    def start(self) -> None:
        if self.process:
            return
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        args = [_tool("ffmpeg"), "-y", "-v", "error", "-f", "gdigrab", "-framerate", str(self.fps), "-i", f"title={self.window_title}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", str(self.out_path)]
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(0.8)
        if self.process.poll() is not None:
            # window not found: record the whole desktop instead
            args[args.index(f"title={self.window_title}")] = "desktop"
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.started_at = time.time()

    def stop(self) -> Path | None:
        if not self.process:
            return None
        try:
            if self.process.stdin:
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()
        self.process = None
        index = self.out_path.with_suffix(".json")
        index.write_text(f'{{"started_at": {self.started_at}}}', encoding="utf-8")
        return self.out_path if self.out_path.is_file() else None

    @property
    def recording(self) -> bool:
        return self.process is not None and self.process.poll() is None
