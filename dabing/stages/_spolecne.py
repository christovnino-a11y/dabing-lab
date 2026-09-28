"""Společné pro těžké kroky, které běží jako samostatné procesy.

Průběh se hlásí řádkem `@@ {"p": 0.42, "zprava": "…"}` na stdout; orchestrátor
(`dabing/pipeline.py`) ho převádí do UI. Ostatní řádky jsou obyčejný log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def prubeh(p: float, zprava: str = "") -> None:
    print("@@ " + json.dumps({"p": round(float(p), 4), "zprava": zprava}, ensure_ascii=False),
          flush=True)


def slozka_z_argv() -> Path:
    if len(sys.argv) < 2:
        raise SystemExit("použití: python -m dabing.stages.<krok> <složka zakázky>")
    return Path(sys.argv[1]).resolve()


def nacti_mono(cesta: Path, sr_cil: int | None = None) -> tuple[np.ndarray, int]:
    x, sr = sf.read(cesta, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr_cil and sr != sr_cil:
        import soxr
        x = soxr.resample(x, sr, sr_cil, quality="HQ").astype(np.float32)
        sr = sr_cil
    return x, sr
