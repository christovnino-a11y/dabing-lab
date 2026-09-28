"""Maska g(t) pro podklad: podklad = originál − g·hlas mluvčího.

g = 1 tam, kde dabovaný mluvčí mluví, 0 jinde, mezi tím kosinové rampy. Kde je
g = 0, hraje originál bit po bitu: publikum, hudba i ruchy zůstanou, jak byly.

Aktivita vychází ze slov přepisu (jen věty s překladem). Konec slova se ořízne
podle detektoru řeči: Parakeet natahuje poslední slovo věty přes následující
ticho nebo potlesk a maska by jinak smazala i ten. Krátké pauzy téhož mluvčího
se vyplní (jinak by podklad v rytmu řeči pumpoval), ale jen když v nich
nenastoupí nový zvuk. Za koncem řeči g doznívá (dozvuk sálu) a stáhne se na
nulu ještě před nástupem nového zvuku v hlasové stopě (smích, potlesk, hudba).
Slova vět bez překladu mají g = 0 vždy — ty mají znít v originále.
"""

from __future__ import annotations

import numpy as np

from .config import (DOZVUK_ODECTU_S, DOZVUK_STOP_DB, NABEH_ODECTU_S, PRED_SLOVEM_S,
                     SLOUCIT_MEZERU_S, VAD_PRAH, VAD_REZERVA_S, VYPLNIT_MEZERU_S, ZA_SLOVEM_S)
from .job import Veta

KROK_VAD_S = 512 / 16000   # rámec Silero VAD (viz stages/vad.py)
RAMEC_S = 0.01             # rámec energie pro hlídání nástupu nového zvuku
NASTUP_RAMCU = 3           # nový zvuk musí vydržet aspoň 30 ms
NASTUP_MIN_DBFS = -60.0    # tišší „nástupy" jsou šum v tichu, ne publikum
MIN_RAMPA_S = 0.01         # g se nikdy nemění skokem (lupnutí v podkladu)
HRANA_CHRANENYCH_S = 0.01


def energie_db(hlas_mono: np.ndarray, sr: int) -> np.ndarray:
    """Energie hlasové stopy po 10ms rámcích (dBFS)."""
    r = int(RAMEC_S * sr)
    n = len(hlas_mono) // r
    return 10 * np.log10((hlas_mono[:n * r].reshape(n, r) ** 2).mean(axis=1) + 1e-12)


def nastup(energie: np.ndarray | None, od_s: float, do_s: float) -> float | None:
    """Čas (s), kdy mezi od_s a do_s v hlasové stopě nastoupí nový zvuk: energie
    vyskočí o DOZVUK_STOP_DB nad dosavadní minimum a vydrží tam aspoň 30 ms.
    Dozvuk řeči jen klesá, takže ho pravidlo nechá doběhnout."""
    if energie is None:
        return None
    i0 = int(np.ceil(od_s / RAMEC_S))
    i1 = min(len(energie), int(do_s / RAMEC_S))
    minimum, nad = np.inf, 0
    for i in range(i0, i1):
        minimum = min(minimum, energie[i])
        vyskok = energie[i] > minimum + DOZVUK_STOP_DB and energie[i] > NASTUP_MIN_DBFS
        nad = nad + 1 if vyskok else 0
        if nad == NASTUP_RAMCU:
            return (i - NASTUP_RAMCU + 1) * RAMEC_S
    return None


def konec_slova(od: float, do: float, vad: np.ndarray | None) -> float:
    """Konec slova podle VAD: konec posledního rámce řeči uvnitř slova, nejvýš `do`."""
    if vad is None:
        return do
    i0 = max(0, int(od / KROK_VAD_S))
    i1 = min(len(vad), int(np.ceil(do / KROK_VAD_S)))
    rec = np.flatnonzero(vad[i0:i1] >= VAD_PRAH)
    if rec.size == 0:
        return do  # VAD slovo neslyšel (tichá řeč) — radši ho odečíst celé
    return max(od, min(do, (i0 + rec[-1] + 1) * KROK_VAD_S + VAD_REZERVA_S))


def _vyplnit(konec: float, od: float, stejny_mluvci: bool, energie: np.ndarray | None) -> bool:
    """Spojit dvě slova do jednoho úseku aktivity?"""
    mezera = od - konec
    if mezera < SLOUCIT_MEZERU_S + PRED_SLOVEM_S + ZA_SLOVEM_S:
        return True  # rezervy by se stejně skoro dotkly
    if not stejny_mluvci or mezera > VYPLNIT_MEZERU_S or energie is None:
        return False
    # pauza v řeči téhož mluvčího: vyplnit, ledaže v ní zazní něco nového (smích, potlesk)
    return nastup(energie, konec, od - PRED_SLOVEM_S) is None


def useky_aktivity(vety: list[Veta], vad: np.ndarray | None,
                   energie: np.ndarray | None = None) -> list[tuple[float, float]]:
    """Úseky (s), kde mluví dabovaný mluvčí: řeč s rezervou před a za, pauzy vyplněné."""
    slova = sorted((od, konec_slova(od, do, vad), v.mluvci)
                   for v in vety for _, od, do in (v.slova or [["", v.start, v.end]]))
    rec: list[list] = []  # [začátek, konec řeči, mluvčí]
    for od, konec, mluvci in slova:
        if rec and (od <= rec[-1][1] or _vyplnit(rec[-1][1], od, mluvci == rec[-1][2], energie)):
            rec[-1][1] = max(rec[-1][1], konec)
            rec[-1][2] = mluvci
        else:
            rec.append([od, konec, mluvci])
    return [(max(0.0, a - PRED_SLOVEM_S), b + ZA_SLOVEM_S) for a, b, _ in rec]


def _kosinus(delka: int, stoupa: bool) -> np.ndarray:
    x = np.linspace(0.0, np.pi, delka, endpoint=False) if stoupa else np.linspace(0.0, np.pi, delka)
    return ((0.5 - 0.5 * np.cos(x)) if stoupa else (0.5 + 0.5 * np.cos(x))).astype(np.float32)


def maska(useky: list[tuple[float, float]], energie: np.ndarray | None, sr: int, n: int,
          chranene: list[tuple[float, float]] = ()) -> np.ndarray:
    """g po vzorcích (float32, délka n) z úseků aktivity; `chranene` = slova, kde g = 0."""
    g = np.zeros(n, dtype=np.float32)
    nabeh = max(1, int(NABEH_ODECTU_S * sr))
    dobeh = max(1, int(DOZVUK_ODECTU_S * sr))
    min_rampa = max(1, int(MIN_RAMPA_S * sr))
    vzestup = _kosinus(nabeh, stoupa=True)
    pokles = _kosinus(dobeh + 1, stoupa=False)[1:]
    for a_s, b_s in useky:
        a, b = int(a_s * sr), min(n, int(round(b_s * sr)))
        if a >= n or b <= a:
            continue
        z0, z1 = max(0, a - nabeh), min(n, b + dobeh)
        obal = np.zeros(z1 - z0, dtype=np.float32)
        obal[:a - z0] = vzestup[nabeh - (a - z0):]
        obal[a - z0:b - z0] = 1.0
        obal[b - z0:] = pokles[:z1 - b]
        # nový zvuk po konci řeči (i během rezervy za slovem): g stáhnout na nulu před ním
        konec_reci = b_s - ZA_SLOVEM_S
        t = nastup(energie, konec_reci, b_s + DOZVUK_ODECTU_S)
        if t is not None:
            r1 = min(int(round(t * sr)), z1)
            r0 = max(int(konec_reci * sr), a, r1 - nabeh)
            r1 = min(z1, max(r1, r0 + min_rampa))
            obal[r0 - z0:r1 - z0] *= _kosinus(r1 - r0, stoupa=False)
            obal[r1 - z0:] = 0.0
        g[z0:z1] = np.maximum(g[z0:z1], obal)
    if chranene:
        hrana = max(1, int(HRANA_CHRANENYCH_S * sr))
        ochrana = np.zeros(n, dtype=np.float32)
        nahoru = _kosinus(hrana, stoupa=True)
        for od, do in chranene:
            a, b = max(0, int(od * sr)), min(n, int(np.ceil(do * sr)))
            if a >= b:
                continue
            ochrana[a:b] = 1.0
            k = min(hrana, a)
            ochrana[a - k:a] = np.maximum(ochrana[a - k:a], nahoru[hrana - k:])
            k = min(hrana, n - b)
            ochrana[b:b + k] = np.maximum(ochrana[b:b + k], nahoru[::-1][:k])
        g *= 1.0 - ochrana
    return g
