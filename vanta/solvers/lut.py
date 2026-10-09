"""3D LUT (.cube) writing, reading, application and transform verification (manual 11.4/11.5)."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vanta.core.colormath import delta_e_2000, rgb_to_lab, test_grid


class LUTError(ValueError):
    pass


def write_cube(
    path: str | Path,
    fn: Callable[[np.ndarray], np.ndarray],
    *,
    size: int = 33,
    title: str = "VANTA",
) -> Path:
    """Sample ``fn`` on an N^3 grid and write an Adobe/Resolve .cube (red varies fastest)."""
    if size < 2 or size > 129:
        raise LUTError("LUT size must be between 2 and 129")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    g = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = np.clip(np.asarray(fn(grid), dtype=np.float64), 0.0, 1.0)
    if out.shape != grid.shape or not np.all(np.isfinite(out)):
        raise LUTError("look function returned an invalid array")
    lines = [f'TITLE "{title[:60]}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    body = "\n".join(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}" for row in out)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n" + body + "\n", encoding="ascii")
    os.replace(tmp, path)
    return path


@dataclass
class Cube:
    size: int
    table: np.ndarray  # shape (B, G, R, 3)
    domain_min: np.ndarray
    domain_max: np.ndarray

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Trilinear interpolation of float RGB in [domain_min, domain_max]."""
        x = np.asarray(rgb, dtype=np.float32)
        shape = x.shape
        flat = x.reshape(-1, 3)
        scaled = (flat - self.domain_min) / (self.domain_max - self.domain_min)
        scaled = np.clip(scaled, 0.0, 1.0) * (self.size - 1)
        i0 = np.floor(scaled).astype(np.int32)
        i0 = np.clip(i0, 0, self.size - 2)
        f = scaled - i0
        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
        t = self.table

        def at(db: int, dg: int, dr: int) -> np.ndarray:
            return t[b0 + db, g0 + dg, r0 + dr]

        c00 = at(0, 0, 0) * (1 - fr) + at(0, 0, 1) * fr
        c01 = at(0, 1, 0) * (1 - fr) + at(0, 1, 1) * fr
        c10 = at(1, 0, 0) * (1 - fr) + at(1, 0, 1) * fr
        c11 = at(1, 1, 0) * (1 - fr) + at(1, 1, 1) * fr
        c0 = c00 * (1 - fg) + c01 * fg
        c1 = c10 * (1 - fg) + c11 * fg
        out = c0 * (1 - fb) + c1 * fb
        return out.reshape(shape).astype(np.float32)


def read_cube(path: str | Path) -> Cube:
    size = 0
    domain_min = np.zeros(3, dtype=np.float32)
    domain_max = np.ones(3, dtype=np.float32)
    rows: list[list[float]] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0].upper()
        if head == "LUT_3D_SIZE":
            size = int(line.split()[1])
        elif head == "LUT_1D_SIZE":
            raise LUTError("1D LUTs are not supported for look verification")
        elif head == "DOMAIN_MIN":
            domain_min = np.asarray([float(v) for v in line.split()[1:4]], dtype=np.float32)
        elif head == "DOMAIN_MAX":
            domain_max = np.asarray([float(v) for v in line.split()[1:4]], dtype=np.float32)
        elif head in {"TITLE", "LUT_3D_INPUT_RANGE"}:
            continue
        else:
            try:
                rows.append([float(v) for v in line.split()[:3]])
            except ValueError:
                continue
    if size < 2 or len(rows) != size**3:
        raise LUTError(f"malformed .cube: size {size}, {len(rows)} rows")
    table = np.asarray(rows, dtype=np.float32).reshape(size, size, size, 3)
    return Cube(size=size, table=table, domain_min=domain_min, domain_max=domain_max)


def lut_monotone_luma(cube: Cube) -> bool:
    """Neutral axis must not invert (unit test guard against broken looks)."""
    axis = np.linspace(0, 1, 64, dtype=np.float32)
    grey = np.stack([axis] * 3, axis=-1)
    out = cube.apply(grey)
    y = out @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return bool(np.all(np.diff(y) >= -1e-3))


def transform_error(
    fn_a: Callable[[np.ndarray], np.ndarray],
    fn_b: Callable[[np.ndarray], np.ndarray],
    *,
    grid_size: int = 9,
) -> dict[str, float]:
    """Mean/p95/max CIEDE2000 between two transforms on a synthetic colour grid.

    Manual 11.5 acceptance: mean error under 1 dE between the exported Resolve
    LUT and the intended transform.
    """
    grid = test_grid(grid_size)
    lab_a = rgb_to_lab(np.clip(fn_a(grid), 0, 1))
    lab_b = rgb_to_lab(np.clip(fn_b(grid), 0, 1))
    de = delta_e_2000(lab_a, lab_b)
    return {
        "mean_de": float(np.mean(de)),
        "p95_de": float(np.percentile(de, 95)),
        "max_de": float(np.max(de)),
        "samples": int(de.size),
    }
