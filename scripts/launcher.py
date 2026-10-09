"""VANTA.exe — native launcher for the desktop app.

Built with PyInstaller (``scripts/build_app.ps1``) into a small windowed
executable that lives in the install folder. It starts the app with the
project's isolated Python environment so the heavy ML stack (CUDA PyTorch,
transformers, ctranslate2) stays a normal, updatable virtualenv.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def _root() -> Path:
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    for candidate in (here, here.parent, here.parent.parent):
        if (candidate / "vanta" / "app" / "main.py").is_file():
            return candidate
    return here


def _message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "VANTA", 0x10)


def main() -> int:
    root = _root()
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        _message(f"VANTA's Python environment was not found in\n{root}\\.venv\n\nRun scripts\\install.ps1 first.")
        return 1
    env = os.environ.copy()
    env.setdefault("VANTA_DATA_DIR", str(root / "data"))
    env.setdefault("HF_HOME", str(root / "data" / "models" / "huggingface"))
    env.setdefault("OLLAMA_NUM_PARALLEL", "1")
    env["PYTHONUTF8"] = "1"
    try:
        subprocess.Popen([str(pythonw), "-m", "vanta.app"], cwd=str(root), env=env, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError as exc:
        _message(f"Could not start VANTA: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
