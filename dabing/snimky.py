"""Pohlaví mluvčího ze snímků videa — pojistka, když ho nejde určit z hlasu.

Hlas rozliší dospělého muže a ženu, u dítěte (nad ~250 Hz) a v pásmu 150–165 Hz ne. Tam se
Codexu ukážou 3 snímky z okamžiků, kdy mluvčí mluví. Je to VOLITELNÉ (zaškrtnutí v UI): jde
o jediný případ, kdy z Macu odchází obraz a ne jen text, a na snímcích bývají obličeje.
Codex smí odpovědět „nejiste" (na snímku nikdo, víc lidí, nejde poznat) — to se nehádá.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .codex_client import Codex, obrazek, text
from .job import Zakazka

POCET_SNIMKU = 3
SCHEMA = {
    "type": "object",
    "properties": {"pohlavi": {"type": "string", "enum": ["m", "z", "nejiste"]},
                   "duvod": {"type": "string"}},
    "required": ["pohlavi", "duvod"], "additionalProperties": False,
}
POKYNY = ("Pomáháš dabingu videa: potřebuješ vědět, v jakém gramatickém rodě má mluvčí o sobě mluvit. "
          "Dostaneš snímky z okamžiků, kdy mluví jeden konkrétní mluvčí. Urči pohlaví osoby, která "
          "na snímcích mluví (muž nebo chlapec = m, žena nebo dívka = z). Odpověz `nejiste`, když "
          "na snímcích není osoba, je jich víc a nepoznáš, která mluví, mluvčí může být hlas za "
          "obrazem (vypravěč, asistent, telefon) a viditelná osoba jen poslouchá, nebo to z obrazu nejde "
          "poznat — nehádej. Nepoužívej žádné nástroje ani soubory. `duvod` česky, jednou větou.")


def ma_obraz(z: Zakazka) -> Path | None:
    """Zdrojové video, je-li to opravdu video (zvuk bez obrazu snímky nemá)."""
    for cesta in z.slozka.glob("source.*"):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_type", "-of", "csv=p=0", str(cesta)],
                           capture_output=True, text=True)
        if r.stdout.strip() == "video":
            return cesta
    return None


def _casy(z: Zakazka, mluvci: int) -> list[float]:
    """Středy nejdelších čistých vět mluvčího, od sebe aspoň 20 s (různé záběry)."""
    vety = sorted((v for v in z.vety if v.mluvci == mluvci and v.cista and v.delka >= 1.0),
                  key=lambda v: -v.delka)
    casy: list[float] = []
    for v in vety:
        t = (v.start + v.end) / 2
        if all(abs(t - c) >= 20 for c in casy):
            casy.append(t)
        if len(casy) == POCET_SNIMKU:
            break
    return sorted(casy)


def pohlavi_ze_snimku(z: Zakazka, mluvci: int, f0: float | None, log) -> tuple[str | None, str]:
    """("m"/"z"/None, důvod). None = Codex nepoznal nebo snímky nejsou."""
    video = ma_obraz(z)
    casy = _casy(z, mluvci)
    if video is None or not casy:
        return None, "není obraz nebo vhodné věty"
    with tempfile.TemporaryDirectory(prefix="dabing-snimky-") as tmp:
        snimky = []
        for i, t in enumerate(casy):
            cil = Path(tmp) / f"snimek_{i}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=640:-2", str(cil)], check=False)
            if cil.exists():
                snimky.append(cil)
        if not snimky:
            return None, "snímky se nepodařilo vyříznout"
        zadani = (f"Mluvčí M{mluvci + 1}. Snímky jsou z časů {', '.join(f'{t:.0f} s' for t in casy)}."
                  + (f" Hlas mluvčího má výšku asi {f0:.0f} Hz." if f0 else ""))
        log(f"Mluvčí M{mluvci + 1}: pohlaví z hlasu nejde určit — Codexu posílám {len(snimky)} snímky videa.")
        with Codex() as codex:
            r = codex.turn([text(zadani), *[obrazek(s) for s in snimky]], schema=SCHEMA, pokyny=POKYNY)
        if r.status != "completed" or not r.text:
            return None, f"Codex neodpověděl ({r.status})"
        data = r.json()
    rod = data.get("pohlavi")
    return (rod if rod in ("m", "z") else None), str(data.get("duvod", ""))
