"""Reference hlasu pro klonování (OmniVoice) — výřezy z čisté hlasové stopy.

Dva druhy:
- **mluvčí**: stálá reference 6–12 s pro každého mluvčího (hlídá identitu hlasu),
- **věta**: původní věta sama (≥ 3 s, bez cizího hlasu v okolí) — nese emoci
  a přednes právě té věty, OmniVoice ji převezme.

Řeže se na hranicích slov z Parakeetu, aby `ref_text` přesně odpovídal zvuku
(jinak klon začne číst text reference).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .config import SR_TTS
from .job import Veta, Zakazka

MIN_VETA_REF_S = 3.0
MAX_VETA_REF_S = 15.0
CIL_MLUVCI_MIN_S = 6.0
CIL_MLUVCI_MAX_S = 12.0
PRED_SLOVEM_S = 0.05
ZA_SLOVEM_S = 0.12
MEZERA_SPOJENI_S = 0.15
CILOVA_HLADINA_DB = -20.0


def _nacti_hlas(zakazka: Zakazka) -> np.ndarray:
    x, sr = sf.read(zakazka.cesta("vocals.wav"), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR_TTS:
        import soxr
        x = soxr.resample(x, sr, SR_TTS, quality="HQ").astype(np.float32)
    return x


def _vyrez(hlas: np.ndarray, od: float, do: float) -> np.ndarray:
    a = max(0, int((od - PRED_SLOVEM_S) * SR_TTS))
    b = min(len(hlas), int((do + ZA_SLOVEM_S) * SR_TTS))
    return hlas[a:b].copy()


def _srovnej_hladinu(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    if rms < 1e-5:
        return x
    zisk = 10 ** (CILOVA_HLADINA_DB / 20) / rms
    y = x * min(zisk, 8.0)
    spicka = float(np.max(np.abs(y))) if y.size else 0.0
    return y / spicka * 0.95 if spicka > 0.95 else y


def _uloz(slozka: Path, jmeno: str, zvuk: np.ndarray, text: str) -> str:
    """Zapíše referenci jen když ještě neexistuje — změna času souboru by zneplatnila
    cache hotových vět (otisk TTS obsahuje mtime reference)."""
    slozka.mkdir(parents=True, exist_ok=True)
    cesta = slozka / f"{jmeno}.wav"
    popis = slozka / f"{jmeno}.txt"
    if not (cesta.exists() and popis.exists() and popis.read_text(encoding="utf-8") == text):
        sf.write(cesta, _srovnej_hladinu(zvuk), SR_TTS, subtype="FLOAT")
        popis.write_text(text, encoding="utf-8")
    return str(cesta.relative_to(slozka.parent))


def _reference_mluvciho(vety: list[Veta], hlas: np.ndarray) -> tuple[np.ndarray, str] | None:
    ciste = [v for v in vety if v.cista and v.delka >= 1.5 and v.slova]
    kandidati = ciste or [v for v in vety if v.delka >= 1.5 and v.slova]
    if not kandidati:
        return None
    jedna = [v for v in kandidati if CIL_MLUVCI_MIN_S <= v.delka <= CIL_MLUVCI_MAX_S]
    if jedna:
        v = min(jedna, key=lambda v: abs(v.delka - 9.0))
        return _vyrez(hlas, v.slova[0][1], v.slova[-1][2]), v.en
    # poskládat z nejdelších čistých vět (v časovém pořadí) do 6–12 s
    vybrane, soucet = [], 0.0
    for v in sorted(kandidati, key=lambda v: -v.delka):
        if soucet + v.delka > CIL_MLUVCI_MAX_S and vybrane:
            continue
        vybrane.append(v)
        soucet += v.delka
        if soucet >= CIL_MLUVCI_MIN_S:
            break
    vybrane.sort(key=lambda v: v.start)
    ticho = np.zeros(int(MEZERA_SPOJENI_S * SR_TTS), dtype=np.float32)
    kusy = []
    for v in vybrane:
        kusy.extend([_vyrez(hlas, v.slova[0][1], v.slova[-1][2]), ticho])
    return np.concatenate(kusy[:-1]), " ".join(v.en for v in vybrane)


def priprav(zakazka: Zakazka) -> dict[int, str]:
    """Vytvoří reference a vyplní `reference`/`druh_reference` u vět.

    Vrací mapu mluvčí → cesta ke stálé referenci (relativně ke složce zakázky).
    """
    hlas = _nacti_hlas(zakazka)
    slozka = zakazka.cesta("refs")
    mluvci_ref: dict[int, str] = {}
    for m in sorted({v.mluvci for v in zakazka.vety}):
        vysledek = _reference_mluvciho([v for v in zakazka.vety if v.mluvci == m], hlas)
        if vysledek is not None:
            mluvci_ref[m] = _uloz(slozka, f"mluvci_{m}", *vysledek)
    nahradni = next(iter(mluvci_ref.values()), "")
    for v in zakazka.vety:
        if v.reference and (zakazka.slozka / v.reference).exists():
            continue  # už přiřazená (i když ji hlídač identity přepnul na stálou)
        stala = mluvci_ref.get(v.mluvci, nahradni)
        if v.cista and v.slova and MIN_VETA_REF_S <= v.delka <= MAX_VETA_REF_S:
            v.reference = _uloz(slozka, f"veta_{v.id:04d}", _vyrez(hlas, v.slova[0][1], v.slova[-1][2]),
                                v.en)
            v.druh_reference = "veta"
        else:
            v.reference, v.druh_reference = stala, "mluvci"
    zakazka.hotovo("reference")
    return mluvci_ref


def text_reference(zakazka: Zakazka, reference: str) -> str:
    return (zakazka.slozka / reference).with_suffix(".txt").read_text(encoding="utf-8")
