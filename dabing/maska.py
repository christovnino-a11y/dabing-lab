"""Maska g(t) pro podklad: podklad = originál − g·hlas mluvčího.

g = 1 tam, kde dabovaný mluvčí mluví, 0 jinde, mezi tím kosinové rampy. Kde je
g = 0, hraje originál bit po bitu: publikum, hudba i ruchy zůstanou, jak byly.

Aktivita vychází ze slov přepisu (jen věty s překladem). Konec slova se ořízne
podle detektoru řeči: Parakeet natahuje poslední slovo věty přes následující
ticho nebo potlesk a maska by jinak smazala i ten. Krátké pauzy se vyplní (jinak
by podklad v rytmu řeči pumpoval), ale jen když v nich nezazní nic nového. Za
koncem řeči g doznívá (dozvuk sálu) a stáhne se na nulu ještě před novým zvukem
v hlasové stopě (smích, potlesk, hudba). Nový zvuk je buď skok energie nad
dosavadní minimum, nebo plató: po konci řeči energie neklesá jako dozvuk, ale
drží (potlesk, který začal už během posledního slova).
Slova vět bez překladu mají g = 0 vždy — ty mají znít v originále.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import (DOZVUK_ODECTU_S, DOZVUK_STOP_DB, NABEH_ODECTU_S, PRED_SLOVEM_S,
                     SLOUCIT_MEZERU_S, VAD_PRAH, VAD_REZERVA_S, VYPLNIT_MEZERU_S, ZA_SLOVEM_S)
from .job import Veta

KROK_VAD_S = 512 / 16000   # rámec Silero VAD (viz stages/vad.py)
RAMEC_S = 0.01             # rámec energie pro hlídání nového zvuku
NASTUP_RAMCU = 3           # nový zvuk musí vydržet aspoň 30 ms
NASTUP_MIN_DBFS = -60.0    # tišší „nástupy" jsou šum v tichu, ne publikum
MIN_RAMPA_S = 0.01         # g se nikdy nemění skokem (lupnutí v podkladu)
HRANA_CHRANENYCH_S = 0.01
# plató: okno 0,1–0,3 s po konci řeči; dozvuk i při RT60 3 s klesá ≥ 20 dB/s
PLATO_OKNO_S = (0.1, 0.3)
PLATO_MAX_POKLES_DB_S = 10.0
PLATO_NAD_PODLAHOU_DB = 6.0     # ustálený ruch sálu ve hlasové stopě není nový zvuk
NADECH_PRED_SLOVEM_S = 0.6      # nádech před slovem pauzu neotevírá…
NADECH_POD_RECI_DB = 15.0       # …když je aspoň o tolik tišší než řeč
RECI_MIMO_MAX_ODSTUP_S = 2.0    # řeč bez věty takhle blízko dabované věty = výplň téhož mluvčího
RECI_MIMO_MIN_S = 0.1           # kratší záblesk detektoru řeči se ignoruje
RECI_MIMO_MAX_S = 2.0           # výplně jsou krátké (Knapek ≤ 1,6 s); delší je zpěv nebo celá řeč
RECI_MIMO_OKOLI_CHRANENYCH_S = 0.15  # běh řeči, který se dotýká věty bez překladu, zůstane celý
EPS = 1e-6


@dataclass
class Kontext:
    """Co maska potřebuje vědět o hlasové stopě (vše po 10ms rámcích, dBFS)."""
    energie: np.ndarray | None
    vad: np.ndarray | None = None
    podlaha_db: float = -120.0   # ustálený ruch mimo řeč
    reci_db: float = -20.0       # typická úroveň řeči
    hladka: np.ndarray | None = None  # energie vyhlazená přes 30 ms (dozvuk po 10 ms kolísá)

    def __post_init__(self) -> None:
        if self.energie is not None and self.hladka is None and len(self.energie):
            vykon = np.convolve(10 ** (self.energie / 10), np.ones(3) / 3, mode="same")
            self.hladka = 10 * np.log10(vykon + 1e-12)

    @classmethod
    def z_hlasu(cls, hlas_mono: np.ndarray, sr: int, vad: np.ndarray | None) -> Kontext:
        r = int(RAMEC_S * sr)
        n = len(hlas_mono) // r
        energie = 10 * np.log10((hlas_mono[:n * r].reshape(n, r) ** 2).mean(axis=1) + 1e-12)
        if vad is not None and len(vad) and n:
            rec = _vad_po_ramcich(vad, n) >= VAD_PRAH
        else:
            rec = energie > np.percentile(energie, 50) if n else np.zeros(0, bool)
        podlaha = float(np.percentile(energie[~rec], 20)) if (~rec).sum() >= 50 else -120.0
        reci = float(np.median(energie[rec])) if rec.sum() >= 50 else -20.0
        return cls(energie, vad, podlaha, reci)


def _vad_po_ramcich(vad: np.ndarray, n: int) -> np.ndarray:
    i = ((np.arange(n) + 0.5) * RAMEC_S / KROK_VAD_S).astype(int)
    return vad[np.minimum(i, len(vad) - 1)]


def _i(t: float) -> int:
    """Index 10ms rámce, který začíná v čase t nebo po něm (tolerance k plovoucí čárce)."""
    return max(0, int(np.ceil(t / RAMEC_S - EPS)))


def nastup(k: Kontext, od_s: float, do_s: float, platne_od_s: float | None = None) -> float | None:
    """Čas (s), kdy mezi od_s a do_s v hlasové stopě nastoupí nový zvuk: energie vyskočí
    o DOZVUK_STOP_DB nad dosavadní minimum a vydrží tam aspoň 30 ms. Skok, který začne
    před `platne_od_s` (v rezervě za koncem řeči), platí jen když nepřetržitě vydrží až
    za `platne_od_s` + 30 ms — koncová souhláska tak neprojde, potlesk ano."""
    if k.hladka is None:
        return None
    e = k.hladka  # na surové energii by kolísání dozvuku sálu vypadalo jako nový zvuk
    i0, i1 = _i(od_s), min(len(e), int(do_s / RAMEC_S))
    platne = _i(platne_od_s) if platne_od_s is not None else i0
    minimum, nad = np.inf, 0
    for i in range(i0, i1):
        minimum = min(minimum, e[i])
        nad = nad + 1 if e[i] > minimum + DOZVUK_STOP_DB and e[i] > NASTUP_MIN_DBFS else 0
        if nad:
            start = i - nad + 1
            if nad >= max(NASTUP_RAMCU, platne + NASTUP_RAMCU - start):
                return start * RAMEC_S
    return None


def plato(k: Kontext, od_s: float, do_s: float) -> float | None:
    """Nový zvuk, který zněl už při konci řeči: energie po konci řeči neklesá jako dozvuk,
    ale drží nad ustáleným ruchem. Vrací čas, kdy dozvuk řeči klesl na úroveň plató."""
    if k.energie is None:
        return None
    e = k.energie
    w0, w1 = _i(od_s + PLATO_OKNO_S[0]), min(len(e), int(min(od_s + PLATO_OKNO_S[1], do_s) / RAMEC_S))
    if w1 - w0 < 10:
        return None
    okno = e[w0:w1]
    median = float(np.median(okno))
    if median < max(NASTUP_MIN_DBFS, k.podlaha_db + PLATO_NAD_PODLAHOU_DB):
        return None
    sklon = float(np.polyfit(np.arange(len(okno)) * RAMEC_S, okno, 1)[0])
    if sklon < -PLATO_MAX_POKLES_DB_S:
        return None  # klesá jako dozvuk
    if k.vad is not None and len(k.vad):
        # od prvního rámce, který začíná až po konci řeči (ten poslední s řečí by vždy „mluvil")
        i = np.arange(int(np.ceil(od_s / KROK_VAD_S - EPS)),
                      min(len(k.vad), int(np.ceil(w1 * RAMEC_S / KROK_VAD_S))))
        if i.size and float(k.vad[i].max()) >= VAD_PRAH:
            return None  # mluvčí ještě mluví
    i0 = _i(od_s)
    pod = np.flatnonzero(e[i0:w1] <= median + 1.5)
    return (i0 + int(pod[0])) * RAMEC_S if pod.size else od_s


def konec_slova(od: float, do: float, vad: np.ndarray | None) -> tuple[float, float]:
    """(konec s rezervou, konec řeči podle VAD bez rezervy), obojí nejvýš `do`."""
    if vad is None or not len(vad):
        return do, do
    i0 = max(0, int(od / KROK_VAD_S))
    i1 = min(len(vad), int(np.ceil(do / KROK_VAD_S)))
    rec = np.flatnonzero(vad[i0:i1] >= VAD_PRAH)
    if rec.size == 0:
        return do, do  # VAD slovo neslyšel (tichá řeč) — radši ho odečíst celé
    konec_vad = max(od, min(do, (i0 + rec[-1] + 1) * KROK_VAD_S))
    return max(od, min(do, konec_vad + VAD_REZERVA_S)), konec_vad


def _nadech(k: Kontext, t: float, od_dalsiho: float) -> bool:
    """Nástup těsně před dalším slovem, výrazně tišší než řeč = nádech mluvčího."""
    if k.energie is None or t < od_dalsiho - PRED_SLOVEM_S - NADECH_PRED_SLOVEM_S:
        return False
    usek = k.energie[_i(t):max(_i(t) + 1, int((od_dalsiho - PRED_SLOVEM_S) / RAMEC_S))]
    # celá událost až k dalšímu slovu (smích s pozvolným náběhem by na začátku prošel)
    uroven = float(np.percentile(usek, 90)) if usek.size else -120.0
    return uroven < k.reci_db - NADECH_POD_RECI_DB


def _vyplnit(konec: float, konec_vad: float, od: float, k: Kontext) -> bool:
    """Spojit dvě slova do jednoho úseku aktivity (vyplnit pauzu mezi nimi)?"""
    mezera = od - konec
    if mezera < SLOUCIT_MEZERU_S + PRED_SLOVEM_S + ZA_SLOVEM_S + EPS:
        return True  # rezervy by se stejně skoro dotkly
    if mezera > VYPLNIT_MEZERU_S + EPS or k.energie is None:
        return False
    # pauza v řeči: vyplnit, ledaže v ní zazní něco nového (smích, potlesk)
    konec_okna = od - PRED_SLOVEM_S
    t = nastup(k, konec_vad, konec_okna, platne_od_s=konec)
    if t is not None and not _nadech(k, t, od):
        return False
    return plato(k, konec_vad, konec_okna) is None


def useky_aktivity(vety: list[Veta], k: Kontext) -> list[tuple[float, float, float]]:
    """Úseky (začátek, konec, konec řeči podle VAD) v s, kde mluví dabovaný mluvčí:
    řeč s rezervou před a za, pauzy vyplněné."""
    slova = sorted((od, *konec_slova(od, do, k.vad))
                   for v in vety for _, od, do in (v.slova or [["", v.start, v.end]]))
    rec: list[list[float]] = []  # [začátek, konec s rezervou, konec podle VAD]
    for od, konec, konec_vad in slova:
        if rec and (od <= rec[-1][1] or _vyplnit(rec[-1][1], rec[-1][2], od, k)):
            if konec > rec[-1][1]:
                rec[-1][1], rec[-1][2] = konec, konec_vad
        else:
            rec.append([od, konec, konec_vad])
    return [(max(0.0, a - PRED_SLOVEM_S), b + ZA_SLOVEM_S, kv) for a, b, kv in rec]


def useky_mimo_vet(useky: list[tuple[float, float, float]], k: Kontext,
                   chranene: list[tuple[float, float]] = (),
                   max_odstup_s: float = RECI_MIMO_MAX_ODSTUP_S) -> list[tuple[float, float, float]]:
    """Řeč podle detektoru řeči mimo slova dabovaných vět, ale nejvýš `max_odstup_s` od nich:
    krátké nepřepsané výplně dabovaného mluvčího („ehm", „no", nedořečená slova). Bez odečtu by
    mezi větami zazněl jeho původní hlas (Knapek 2026-09-30: 114 úseků, ~94 s). Zůstává
    v originále: řeč daleko od dabovaných vět, dlouhé úseky (zpěv v hudbě, nepřepsaný monolog)
    a celý běh řeči, ve kterém leží věta bez překladu (jinak by se její okolí rozsekalo)."""
    if k.vad is None or not len(k.vad) or not useky:
        return []
    rec = np.concatenate(([False], k.vad >= VAD_PRAH, [False]))
    hrany = np.flatnonzero(rec[1:] != rec[:-1])
    zacatky = np.array([a for a, _, _ in useky])
    konce = np.array([b for _, b, _ in useky])
    vysledek: list[tuple[float, float, float]] = []
    for i0, i1 in zip(hrany[::2], hrany[1::2]):
        beh_od, beh_do = i0 * KROK_VAD_S, i1 * KROK_VAD_S
        if any(c - RECI_MIMO_OKOLI_CHRANENYCH_S < beh_do and d + RECI_MIMO_OKOLI_CHRANENYCH_S > beh_od
               for c, d in chranene):
            continue
        # z běhu řeči jen části, které žádný úsek aktivity nepokrývá
        kusy = [(beh_od, beh_do)]
        for a, b in zip(zacatky, konce):
            kusy = [c for x, y in kusy
                    for c in (((x, y),) if b <= x or a >= y else ((x, a), (b, y))) if c[1] > c[0]]
        for od, do in kusy:
            if not RECI_MIMO_MIN_S <= do - od <= RECI_MIMO_MAX_S:
                continue
            if float(np.maximum(0.0, np.maximum(zacatky - do, od - konce)).min()) > max_odstup_s:
                continue
            vysledek.append((max(0.0, od - PRED_SLOVEM_S), do + VAD_REZERVA_S, do))
    return vysledek


def _kosinus(delka: int, stoupa: bool) -> np.ndarray:
    x = np.linspace(0.0, np.pi, delka, endpoint=False) if stoupa else np.linspace(0.0, np.pi, delka)
    return ((0.5 - 0.5 * np.cos(x)) if stoupa else (0.5 + 0.5 * np.cos(x))).astype(np.float32)


def maska(useky: list[tuple[float, float, float]], k: Kontext, sr: int, n: int,
          chranene: list[tuple[float, float]] = ()) -> np.ndarray:
    """g po vzorcích (float32, délka n) z úseků aktivity; `chranene` = úseky, kde g = 0."""
    g = np.zeros(n, dtype=np.float32)
    nabeh = max(1, int(NABEH_ODECTU_S * sr))
    dobeh = max(1, int(DOZVUK_ODECTU_S * sr))
    min_rampa = max(1, int(MIN_RAMPA_S * sr))
    vzestup = _kosinus(nabeh, stoupa=True)
    pokles = _kosinus(dobeh + 1, stoupa=False)[1:]
    for a_s, b_s, konec_vad in useky:
        a, b = int(a_s * sr), min(n, int(round(b_s * sr)))
        if a >= n or b <= a:
            continue
        z0, z1 = max(0, a - nabeh), min(n, b + dobeh)
        obal = np.zeros(z1 - z0, dtype=np.float32)
        obal[:a - z0] = vzestup[nabeh - (a - z0):]
        obal[a - z0:b - z0] = 1.0
        obal[b - z0:] = pokles[:z1 - b]
        # nový zvuk po konci řeči (i v rezervě za slovem): g stáhnout na nulu před ním
        konec_do = b_s + DOZVUK_ODECTU_S
        t = nastup(k, konec_vad, konec_do, platne_od_s=b_s - ZA_SLOVEM_S)
        if t is None:
            t = plato(k, konec_vad, konec_do)
        if t is not None:
            r1 = min(int(round(t * sr)), z1)
            r0 = max(int(konec_vad * sr), a, r1 - nabeh)
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
            h = min(hrana, a)
            ochrana[a - h:a] = np.maximum(ochrana[a - h:a], nahoru[hrana - h:])
            h = min(hrana, n - b)
            ochrana[b:b + h] = np.maximum(ochrana[b:b + h], nahoru[::-1][:h])
        g *= 1.0 - ochrana
    return g
