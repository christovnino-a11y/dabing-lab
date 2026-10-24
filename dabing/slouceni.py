"""Sloučení mluvčích, které diarizace zbytečně rozdělila — jeden člověk ve dvou mluvčích.

Opak `rozdeleni.py`. Diarizace odštěpí od řečníka chvíle, kdy hlas změní: NVIDIA GTC, Jensen
Huang (M4, 1025 vět) a „M5" = 8 jeho výkřiků („Math is beautiful", „I want to thank all of
you") s výškou 106–295 Hz. Z výšky by M5 vyšla jako žena a dostala vlastní klon z výkřiků.
Otisk hlasu (ECAPA) se s výškou ani hlasitostí nemění: M5 × M4 = 0,70, vypravěčka promo videí
M1 × M7 = 0,84, různí lidé v témže videu 0,0–0,35. Mluvčí s podobností nad prahem a jasným
náskokem před ostatními se připojí k většímu z dvojice (jeho reference, klon i pohlaví) — ale jen
VĚTY, které jsou otiskem hlasu blízko cíli. V M5 byla i žena z hlediště, která na Huanga křičela
(23:23 „What are you doing about that?", podobnost s Huangem −0,04): ta zůstane samostatná.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import soundfile as sf

from .job import Zakazka
from .rozdeleni import SR, _vystrihni
from .tts import Worker

PRAH_SLOUCENI = 0.65    # různí lidé ≤ 0,35, týž člověk 0,70–0,85 (GTC 2026); IDENTITA_JINY_MLUVCI_MAX 0,55
MIN_NASKOK = 0.15       # o tolik musí být nejbližší mluvčí blíž než druhý nejbližší
PRAH_VETY = 0.15       # věta pod tímhle k cíli nepatří (žena z hlediště −0,04; Huangovo krátké „děkuji" 0,18)
MAX_VZOR_S = 25.0
MIN_VETA_S = 1.0
MAX_ODSTUP_S = 10.0     # krátká (neměřitelná) věta jde s nejbližší změřenou větou, je-li tak blízko


def sloucit(z: Zakazka, zmereno: dict, log) -> dict[str, tuple[str, list[int]]]:
    """{mluvčí: (k_němu_připojit, [id vět])} pro mluvčí, kteří jsou podle otisku hlasu týž člověk
    jako jiný (větší) mluvčí. Výsledek ověření si zapíše do zmereno[m]["slouceni_overeno"]."""
    mluvci = sorted({str(v.mluvci) for v in z.vety}, key=int)
    if len(mluvci) < 2 or all((zmereno.get(m) or {}).get("slouceni_overeno") for m in mluvci):
        return {}
    hlas, sr = sf.read(str(z.cesta("vocals16.wav")), dtype="float32")
    hlas = hlas if hlas.ndim == 1 else hlas.mean(axis=1)
    if sr != SR:
        raise RuntimeError(f"vocals16.wav má {sr} Hz, čekám {SR}")
    pocet = {m: sum(1 for v in z.vety if str(v.mluvci) == m) for m in mluvci}
    w = Worker(log, z.slozka.parent.parent / "logy" / "omnivoice.log")
    w.start()
    try:
        with tempfile.TemporaryDirectory(prefix="dabing-slouceni-") as tmp:
            vzory: dict[str, Path] = {}
            for m in mluvci:
                vety = sorted((v for v in z.vety if str(v.mluvci) == m and v.end - v.start >= MIN_VETA_S),
                              key=lambda v: v.start - v.end)  # nejdelší první
                if vety:
                    vzory[m] = Path(tmp) / f"m{m}.wav"
                    _vystrihni(hlas, vety, vzory[m], MAX_VZOR_S)
            podobnost: dict[tuple[str, str], float] = {}

            def podobne(a: Path, b: Path) -> float | None:
                o = w.dotaz({"op": "identita", "a": str(a), "b": str(b)}, timeout=120.0)
                return float(o["podobnost"]) if o.get("ok") and o.get("podobnost") is not None else None

            for i, a in enumerate(vzory):
                for b in list(vzory)[i + 1:]:
                    if (p := podobne(vzory[a], vzory[b])) is not None:
                        podobnost[(a, b)] = podobnost[(b, a)] = p
            for m in mluvci:
                zmereno.setdefault(m, {})["slouceni_overeno"] = True
            # rozdělení (rozdeleni.py / pohlavi.smisene) právě oddělilo dva lidi — zpátky je nespojovat
            oddelene = {(m, x["oddeleno_od"]) for m, x in zmereno.items() if x.get("oddeleno_od")}
            k_cemu: dict[str, str] = {}
            for m in sorted(vzory, key=lambda m: pocet[m]):  # od nejmenšího
                poradi = sorted(((p, b) for (a, b), p in podobnost.items() if a == m), reverse=True)
                if not poradi:
                    continue
                nejlepsi, cil = poradi[0]
                druhy = poradi[1][0] if len(poradi) > 1 else 0.0
                if (nejlepsi >= PRAH_SLOUCENI and nejlepsi - druhy >= MIN_NASKOK and pocet[cil] >= pocet[m]
                        and (m, cil) not in oddelene and (cil, m) not in oddelene):
                    k_cemu[m] = cil
            vysledek: dict[str, tuple[str, list[int]]] = {}
            for m, cil in k_cemu.items():
                while cil in k_cemu and k_cemu[cil] != m:  # M7 → M1 → … až ke konečnému mluvčímu
                    cil = k_cemu[cil]
                vety = [v for v in z.vety if str(v.mluvci) == m]
                patri: dict[int, bool] = {}
                for v in vety:  # každá věta zvlášť: ve slučovaném mluvčím může být i cizí hlas
                    if v.end - v.start >= MIN_VETA_S:
                        kus = Path(tmp) / f"veta_{v.id}.wav"
                        _vystrihni(hlas, [v], kus)
                        p = podobne(kus, vzory[cil])
                        if p is not None:
                            patri[v.id] = p >= PRAH_VETY
                body = sorted((v.start, patri[v.id]) for v in vety if v.id in patri)
                ids = []
                for v in vety:
                    if v.id in patri:
                        ano = patri[v.id]
                    else:
                        d, ano = min(((abs(v.start - t), b) for t, b in body), default=(1e9, False))
                        ano = ano and d <= MAX_ODSTUP_S
                    if ano:
                        ids.append(v.id)
                if ids:
                    vysledek[m] = (cil, ids)
                    zbyva = len(vety) - len(ids)
                    log(f"Mluvčí M{int(m) + 1} je podle otisku hlasu týž člověk jako M{int(cil) + 1} "
                        f"(podobnost {podobnost[(m, k_cemu[m])]:.2f}) — připojuji {len(ids)} vět"
                        + (f", {zbyva} s jiným hlasem zůstává" if zbyva else "") + ".")
    finally:
        w.zavri()
    return vysledek
