"""Rozdělení mluvčího, ve kterém diarizace slila dva lidi s PODOBNĚ VYSOKÝM hlasem — podle otisku
hlasu (ECAPA), ne podle výšky.

Krok pohlaví (stages/pohlavi.py) rozdělí mluvčího jen tehdy, když jeho věty tvoří dvě oddělené
skupiny výšky hlasu. Muž a žena kolem hranice (muž ~150 Hz, žena ~180 Hz) se ale překrývají:
Gemini at Work, M5 = 362 vět, z toho 81 ženských uprostřed mužských → jeden klon na oba, dabing
„přeskakuje" mezi ženským a mužským hlasem. Tady se z nejnižších a nejvyšších vět mluvčího složí
dva vzory; když jsou to podle ECAPA dva různí lidé, každá věta se přiřadí k bližšímu vzoru.
Výška hlasu jen vybere kandidáty a vzory — o rozdělení rozhoduje otisk hlasu.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .job import Zakazka
from .tts import Worker

SR = 16000
MIN_VET = 20                # menší mluvčí se nerozkládá (málo dat)
MIN_ROZPETI_HZ = 35.0       # P10–P90 výšky hlasu; jeden člověk mívá užší rozsah
PODIL_VZORU = 0.15          # nejnižší / nejvyšší podíl vět jako vzor
MIN_VZOR_VET = 5
MAX_VZOR_S = 25.0
MIN_ROZDIL_VZORU_HZ = 25.0
MAX_PODOBNOST_VZORU = 0.55  # vzory podobnější než tohle = týž člověk, nerozkládat
ROZDIL_PRIRAZENI = 0.04     # věta patří ke vzoru, jen když je mu o tolik blíž
MIN_VETA_S = 1.0
MIN_SKUPINA = 8
MIN_PODIL = 0.10


def kandidati(zmereno: dict) -> list[str]:
    """Mluvčí se širokým rozsahem výšky hlasu, kteří ještě nebyli ověřeni otiskem hlasu."""
    vysledek = []
    for m, x in zmereno.items():
        f = np.array(list((x.get("vety") or {}).values()), dtype=float)
        if x.get("hlas_overen") or len(f) < MIN_VET:
            continue
        if np.percentile(f, 90) - np.percentile(f, 10) >= MIN_ROZPETI_HZ:
            vysledek.append(m)
    return vysledek


def _vystrihni(hlas: np.ndarray, vety, cil: Path, max_s: float | None = None) -> float:
    kusy, celkem = [], 0.0
    ticho = np.zeros(int(0.2 * SR), dtype=np.float32)
    for v in vety:
        if max_s is not None and celkem >= max_s:
            break
        kusy += [hlas[int(v.start * SR):int(v.end * SR)], ticho]
        celkem += v.end - v.start
    sf.write(str(cil), np.concatenate(kusy) if kusy else ticho, SR)
    return celkem


def rozdel(z: Zakazka, zmereno: dict, log) -> dict[str, list[int]]:
    """{mluvčí: [id vět druhého člověka]} pro mluvčí, kteří jsou podle otisku hlasu dva lidé.
    U každého prověřeného mluvčího nastaví zmereno[m]["hlas_overen"] = True."""
    mluvci = kandidati(zmereno)
    if not mluvci:
        return {}
    hlas, sr = sf.read(str(z.cesta("vocals16.wav")), dtype="float32")
    hlas = hlas if hlas.ndim == 1 else hlas.mean(axis=1)
    if sr != SR:
        raise RuntimeError(f"vocals16.wav má {sr} Hz, čekám {SR}")
    vysledek: dict[str, list[int]] = {}
    w = Worker(log, z.slozka.parent.parent / "logy" / "omnivoice.log")
    w.start()
    try:
        with tempfile.TemporaryDirectory(prefix="dabing-rozdeleni-") as tmp:
            tmp = Path(tmp)

            def podobnost(a: Path, b: Path) -> float | None:
                o = w.dotaz({"op": "identita", "a": str(a), "b": str(b)}, timeout=120.0)
                return o.get("podobnost") if o.get("ok") else None

            for m in mluvci:
                zmereno[m]["hlas_overen"] = True
                f0 = {int(k): f for k, f in zmereno[m]["vety"].items()}
                vety = {v.id: v for v in z.vety if str(v.mluvci) == m}
                serazene = sorted((i for i in f0 if i in vety), key=lambda i: f0[i])
                n = max(MIN_VZOR_VET, int(len(serazene) * PODIL_VZORU))
                nizke, vysoke = serazene[:n], serazene[-n:]
                if np.median([f0[i] for i in vysoke]) - np.median([f0[i] for i in nizke]) < MIN_ROZDIL_VZORU_HZ:
                    continue
                vzor_a, vzor_b = tmp / f"{m}_a.wav", tmp / f"{m}_b.wav"
                delsi = lambda ids: sorted((vety[i] for i in ids), key=lambda v: v.start - v.end)  # noqa: E731
                _vystrihni(hlas, delsi(nizke), vzor_a, MAX_VZOR_S)
                _vystrihni(hlas, delsi(vysoke), vzor_b, MAX_VZOR_S)
                mezi = podobnost(vzor_a, vzor_b)
                if mezi is None or mezi >= MAX_PODOBNOST_VZORU:
                    log(f"Mluvčí M{int(m) + 1}: široký rozsah hlasu, ale podle otisku hlasu je to jeden "
                        f"člověk (podobnost vzorů {mezi}).")
                    continue
                prirazeni: dict[int, str] = {}
                for v in vety.values():
                    if v.end - v.start < MIN_VETA_S:
                        continue
                    kus = tmp / f"veta_{v.id}.wav"
                    _vystrihni(hlas, [v], kus)
                    sa, sb = podobnost(kus, vzor_a), podobnost(kus, vzor_b)
                    kus.unlink(missing_ok=True)
                    if sa is not None and sb is not None and abs(sa - sb) > ROZDIL_PRIRAZENI:
                        prirazeni[v.id] = "a" if sa > sb else "b"
                body = sorted((vety[i].start, g) for i, g in prirazeni.items())
                for v in vety.values():  # krátké a nerozhodnuté věty k nejbližší přiřazené (do 10 s)
                    if v.id not in prirazeni and body:
                        d, g = min((abs(v.start - t), g) for t, g in body)
                        if d <= 10.0:
                            prirazeni[v.id] = g
                a = [i for i, g in prirazeni.items() if g == "a"]
                b = [i for i, g in prirazeni.items() if g == "b"]
                mensi = min(a, b, key=len)
                if len(mensi) < MIN_SKUPINA or len(mensi) / len(vety) < MIN_PODIL:
                    log(f"Mluvčí M{int(m) + 1}: otisk hlasu dva lidi nerozlišil dost jasně "
                        f"({len(a)} × {len(b)} vět) — nechávám jako jednoho.")
                    continue
                log(f"Mluvčí M{int(m) + 1} jsou podle otisku hlasu dva lidé (podobnost vzorů {mezi:.2f}): "
                    f"{len(a)} a {len(b)} vět.")
                vysledek[m] = sorted(mensi)
    finally:
        w.zavri()
    return vysledek
