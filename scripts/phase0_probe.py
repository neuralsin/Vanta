"""Probe VANTA's real local capabilities without starting external software."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESOLVE_ROOTS = (
    Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve"),
    Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve Studio"),
)


def _powershell_json(script: str) -> Any:
    if not shutil.which("powershell") and not shutil.which("pwsh"):
        return None
    shell = shutil.which("pwsh") or shutil.which("powershell")
    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    return None


def _hardware() -> dict[str, Any]:
    data = _powershell_json(
        "$os=Get-CimInstance Win32_OperatingSystem; $g=@(Get-CimInstance Win32_VideoController | "
        "ForEach-Object {@{name=$_.Name;adapter_ram_bytes=$_.AdapterRAM}}); "
        "@{os=$os.Caption;os_version=$os.Version;ram_bytes=[int64]$os.TotalVisibleMemorySize*1024; "
        "free_ram_bytes=[int64]$os.FreePhysicalMemory*1024;gpus=$g} | ConvertTo-Json -Depth 4 -Compress"
    )
    if not isinstance(data, dict):
        return {"detected": False, "reason": "Windows CIM query unavailable"}
    gpus = data.get("gpus", [])
    if isinstance(gpus, dict):
        gpus = [gpus]
    result = {
        "detected": True,
        "os": data.get("os"),
        "os_version": data.get("os_version"),
        "ram_bytes": data.get("ram_bytes"),
        "free_ram_bytes": data.get("free_ram_bytes"),
        "gpus": gpus,
        "note": "Win32_VideoController.AdapterRAM is a Windows-reported value; verify with nvidia-smi before model scheduling.",
    }
    smi = _nvidia_smi()
    if smi:
        result["nvidia_smi"] = smi
        for index, gpu in enumerate(result["gpus"]):
            if index < len(smi):
                gpu["vram_total_bytes"] = smi[index]["memory_total_bytes"]
                gpu["vram_free_bytes"] = smi[index]["memory_free_bytes"]
                gpu["driver_version"] = smi[index]["driver_version"]
    return result


def _nvidia_smi() -> list[dict[str, Any]]:
    """Use NVIDIA's own driver query when present; WMI can under-report laptop VRAM."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.splitlines():
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 4:
                continue
            total_mib, free_mib = int(fields[1]), int(fields[2])
            gpus.append(
                {
                    "name": fields[0],
                    "memory_total_bytes": total_mib * 1024**2,
                    "memory_free_bytes": free_mib * 1024**2,
                    "driver_version": fields[3],
                }
            )
        return gpus
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []


def _fixed_disks() -> list[dict[str, Any]]:
    rows = _powershell_json(
        "@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | "
        "Select-Object DeviceID,Size,FreeSpace) | ConvertTo-Json -Depth 3 -Compress"
    )
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return []
    return [
        {
            "drive": row.get("DeviceID"),
            "size_bytes": row.get("Size"),
            "free_bytes": row.get("FreeSpace"),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _resolve() -> dict[str, Any]:
    for root in RESOLVE_ROOTS:
        exe = root / "Resolve.exe"
        if not exe.is_file():
            continue
        scripting = root / "ResolvePython" / "lib" / "modules" / "DaVinciResolveScript.py"
        embedded_python = root / "ResolvePython" / "ResolvePython.exe"
        mcp = root / "DaVinciResolve.mcpb"
        mcp_manifest: dict[str, Any] = {}
        if mcp.is_file():
            try:
                with zipfile.ZipFile(mcp) as archive:
                    mcp_manifest = json.loads(archive.read("manifest.json"))
            except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                mcp_manifest = {}
        version = None
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"(Get-Item -LiteralPath '{exe}').VersionInfo.ProductVersion",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            version = result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
        embedded_python_version = None
        if embedded_python.is_file():
            try:
                result = subprocess.run(
                    [str(embedded_python), "-c", "import sys; print(sys.version.split()[0])"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                    cwd=str(root),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                embedded_python_version = result.stdout.strip() or None
            except (OSError, subprocess.TimeoutExpired):
                pass
        running = _powershell_json(
            "@(Get-Process Resolve -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) "
            "| ConvertTo-Json -Compress"
        )
        if isinstance(running, int):
            running = [running]
        return {
            "installed": True,
            "root": str(root),
            "executable": str(exe),
            "version": version,
            "script_module": str(scripting) if scripting.is_file() else None,
            "embedded_python": str(embedded_python) if embedded_python.is_file() else None,
            "embedded_python_version": embedded_python_version,
            "mcp_binary": str(root / "ResolveMCP.exe")
            if (root / "ResolveMCP.exe").is_file()
            else None,
            "mcp_bundle": str(mcp) if mcp.is_file() else None,
            "mcp_tools": [
                tool.get("name") for tool in mcp_manifest.get("tools", []) if isinstance(tool, dict)
            ],
            "mcp_tool_count": len(mcp_manifest.get("tools", [])),
            "mcp_package_license": mcp_manifest.get("license"),
            "process_ids": running if isinstance(running, list) else [],
            "studio_license_confirmed": False,
            "mcp_live_connection_confirmed": False,
        }
    return {"installed": False, "studio_license_confirmed": False}


def _registry() -> dict[str, Any]:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    font_files = sorted(p.name for p in fonts.glob("*") if p.is_file()) if fonts.exists() else []
    resolve = _resolve()
    root = Path(resolve["root"]) if resolve.get("root") else None
    lut_files: list[str] = []
    if root:
        for folder in (root / "CanonLUTs", root / "LUT", root / "LUTs"):
            if folder.exists():
                lut_files.extend(str(p) for p in folder.rglob("*") if p.is_file())
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "fonts": {"source": str(fonts), "count": len(font_files), "files": font_files},
        "luts": {"count": len(lut_files), "files": sorted(lut_files)},
        "dctls": {"count": 0, "files": [], "status": "not_scanned"},
        "fusion_templates": {"count": 0, "files": [], "status": "not_scanned"},
        "plugins": {"count": 0, "files": [], "status": "not_scanned"},
        "warning": "A filename scan is not a compatibility or licence verification.",
    }


def _gate(gate_id: str, status: str, reason: str, fallback: str | None = None) -> dict[str, Any]:
    gate = {"status": status, "reason": reason}
    if fallback:
        gate["fallback"] = fallback
    return gate


def probe() -> dict[str, Any]:
    resolve = _resolve()
    hardware = _hardware()
    commands = {name: shutil.which(name) for name in ("ffmpeg", "ollama", "nvidia-smi", "git")}
    modules = {
        name: bool(importlib.util.find_spec(name))
        for name in (
            "fastapi",
            "pydantic",
            "yaml",
            "pytest",
            "uvicorn",
            "torch",
            "transformers",
            "airllm",
        )
    }
    total_ram = hardware.get("ram_bytes") if hardware.get("detected") else None
    gates = {
        "G1": _gate(
            "G1",
            "BLOCKED",
            "Resolve is not running; clip import, exact source-frame trims, track targeting, marker read-back, and idempotent rerun remain untested.",
            "FCPXML/OTIO import with post-import verification",
        ),
        "G2": _gate(
            "G2",
            "BLOCKED",
            "Resolve is not running; interchange cuts and dissolves have not been exercised.",
            "Hard cuts plus a verified FCPXML dissolve",
        ),
        "G3": _gate(
            "G3",
            "BLOCKED",
            "Fusion template import, token replacement, render, OCR, and 2 px placement criteria have not been exercised.",
            "Pre-rendered overlay with OCR verification",
        ),
        "G3b": _gate(
            "G3b",
            "BLOCKED",
            "Subject matte occlusion and halo tolerance have not been measured.",
            "Pre-composite outside Resolve",
        ),
        "G4": _gate(
            "G4",
            "BLOCKED",
            "DRX/CDL/LUT writes and exported-LUT colour error have not been measured.",
            "LUT-only grade with export verification",
        ),
        "G5": _gate(
            "G5",
            "BLOCKED",
            "Per-shot external matte or authored DRX-window isolation has not been verified.",
            "Global grade flagged for human review",
        ),
        "G6": _gate(
            "G6",
            "BLOCKED",
            "Matte media import and frame alignment have not been tested.",
            "Pre-composited clip",
        ),
        "G8": _gate(
            "G8",
            "BLOCKED",
            "Word deletion, rebuild, phoneme boundaries, and A/V sync are untested.",
            "Resolve transcription panel with manual review",
        ),
        "G9": _gate(
            "G9",
            "BLOCKED",
            "No three-reel stage-time or peak-VRAM benchmark exists.",
            "Run sequential low-VRAM jobs and record actual timings",
        ),
        "G10": _gate(
            "G10",
            "PARTIAL" if resolve.get("version", "").startswith("21.1") else "BLOCKED",
            f"Installed Resolve version: {resolve.get('version') or 'unknown'}; gate ledger enforcement is not yet active.",
            "Refuse untested versions",
        ),
        "G11": _gate(
            "G11",
            "BLOCKED",
            "No 20-step screen-recorded live-follow run has been measured.",
            "STEP mode, markers, clip colours, and Mission Control thumbnails",
        ),
        "G12": _gate(
            "G12",
            "BLOCKED",
            "Resolve transition API call and exact duration have not been verified.",
            "FCPXML dissolve; Fusion for stylised transitions",
        ),
        "G13": _gate(
            "G13",
            "BLOCKED",
            "Constant-speed and ramp APIs have not been exercised.",
            "FCPXML time map or pre-rendered ramp",
        ),
        "G14": _gate(
            "G14",
            "BLOCKED",
            "External camera solve import and 3-second slip test have not been run.",
            "2.5D parallax",
        ),
        "G15": _gate(
            "G15",
            "PARTIAL",
            "Font and bundled LUT filenames can be inventoried; plugins, DCTLs, Fusion templates, compatibility, and licence checks are not complete.",
            "Only select verified registry entries",
        ),
        "G16": _gate(
            "G16",
            "BLOCKED",
            "Failure-injection and rollback scenarios have not been run against Resolve/MCP.",
            "Keep changes isolated on a new timeline; stop and mark RETRYABLE",
        ),
    }
    disk = shutil.disk_usage(ROOT)
    fixed_disks = _fixed_disks()
    all_fixed_free = sum(int(item.get("free_bytes") or 0) for item in fixed_disks)
    cuda = None
    if modules["torch"]:
        try:
            import torch  # type: ignore[import-not-found]

            cuda = {"available": torch.cuda.is_available(), "devices": []}
            if torch.cuda.is_available():
                cuda["devices"] = [
                    {
                        "name": torch.cuda.get_device_name(i),
                        "vram_bytes": torch.cuda.get_device_properties(i).total_memory,
                    }
                    for i in range(torch.cuda.device_count())
                ]
        except (
            RuntimeError,
            OSError,
            AttributeError,
        ) as exc:  # keep the rest of Phase 0 report usable
            cuda = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace": str(ROOT),
        "python": {
            "version": platform.python_version(),
            "supported_by_manual": sys.version_info >= (3, 12),
        },
        "hardware": hardware,
        "resource_budget": {
            "ram_bytes": total_ram,
            "ram_target_met": bool(total_ram and total_ram >= 32 * 1024**3),
            "gpu_target_vram_bytes": 24 * 1024**3,
            "gpu_primary_total_vram_bytes": (hardware.get("gpus") or [{}])[0].get(
                "vram_total_bytes"
            ),
            "gpu_cuda_probe": cuda,
            "free_disk_bytes": disk.free,
            "all_fixed_disk_free_bytes": all_fixed_free or None,
            "all_fixed_disks": fixed_disks,
            "disk_target_190gb_met": all_fixed_free >= 190 * 1024**3,
            "model_policy": "single heavy model, sequential GPU lease, no Resolve Neural Engine concurrency; no automatic downloads",
        },
        "resolve": resolve,
        "dependencies": {"commands": commands, "python_modules": modules},
        "registry": _registry(),
        "gates": gates,
        "paths": {
            "structure": "append_to_timeline_or_fcpxml",
            "transitions": "unverified_fallback_fcpxml",
            "grade_primary": "unverified_template_drx_plus_lut",
            "grade_secondary": "unverified_cdl",
            "speed_ramp": "unverified_fcpxml_time_map",
            "text_3d": "2_5d_until_G14_passes",
        },
        "summary": {
            "pass": sum(g["status"] == "PASS" for g in gates.values()),
            "partial": sum(g["status"] == "PARTIAL" for g in gates.values()),
            "blocked": sum(g["status"] == "BLOCKED" for g in gates.values()),
            "not_run": sum(g["status"] == "NOT_RUN" for g in gates.values()),
        },
    }


def main() -> int:
    report = probe()
    config = ROOT / "configs"
    config.mkdir(parents=True, exist_ok=True)
    json_path = config / "capabilities.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]

        (ROOT / "capabilities.yaml").write_text(
            yaml.safe_dump(report, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    except ImportError:
        (ROOT / "capabilities.yaml").write_text(
            "# PyYAML is unavailable; canonical machine-readable report is configs/capabilities.json\n"
            "schema_version: '1.0.0'\nstatus: probe_completed_yaml_unavailable\n",
            encoding="utf-8",
        )
    registry_path = ROOT / "plugin_registry.json"
    registry_path.write_text(json.dumps(report["registry"], indent=2), encoding="utf-8")
    print(f"Wrote {ROOT / 'capabilities.yaml'} and {json_path}")
    print("Gate summary: " + json.dumps(report["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
