"""Detekce řeči v hlasové stopě (Silero VAD, CPU) pro masku podkladu.

Vstup:  vocals16.wav (16 kHz mono)
Výstup: vad.npy — pravděpodobnost řeči po 32ms rámcích (float16)

Parakeet natahuje poslední slovo věty přes následující ticho nebo potlesk. Podle
VAD se konce slov oříznou, aby se hlas neodečítal tam, kde už mluvčí mlčí.
Model leží v cache torch.hub (`SILERO_JIT`), nic se nestahuje. Když chybí,
krok nic nezapíše a mix použije jen časy slov (s varováním v logu).
"""

from __future__ import annotations

import os

import numpy as np
import torch

from dabing.config import SILERO_JIT
from dabing.stages._spolecne import nacti_mono, prubeh, slozka_z_argv

SR = 16000
OKNO = 512  # Silero při 16 kHz bere právě 512 vzorků (32 ms)


def main() -> None:
    slozka = slozka_z_argv()
    vstup, cil = slozka / "vocals16.wav", slozka / "vad.npy"
    if cil.exists() and cil.stat().st_mtime >= vstup.stat().st_mtime:
        prubeh(1.0, "Detekce řeči už je hotová")
        return
    if not SILERO_JIT.exists():
        print(f"Silero VAD chybí ({SILERO_JIT}) — maska podkladu pojede jen podle slov.")
        prubeh(1.0, "Detekce řeči přeskočena")
        return

    prubeh(0.0, "Hledám, kde mluvčí opravdu mluví…")
    torch.set_num_threads(1)
    model = torch.jit.load(str(SILERO_JIT), map_location="cpu")
    model.train(False)
    x, _ = nacti_mono(vstup, SR)
    n = len(x) // OKNO
    p = np.zeros(n, dtype=np.float16)
    model.reset_states()
    with torch.no_grad():
        for i in range(n):
            p[i] = model(torch.from_numpy(x[i * OKNO:(i + 1) * OKNO]), SR).item()
            if i % 2000 == 0:
                prubeh(i / max(n, 1), "Hledám, kde mluvčí opravdu mluví…")
    docasny = cil.with_name("vad.tmp.npy")
    np.save(docasny, p)
    os.replace(docasny, cil)
    prubeh(1.0, f"Řeč v {float((p >= 0.5).mean()) * 100:.0f} % času")


if __name__ == "__main__":
    main()
