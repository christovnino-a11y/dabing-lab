"""Z přepisu (asr.json) a mluvčích (diar.json) udělá věty pro dabing.

Věty se skládají ze souvislého seznamu slov: konec věty (. ? !) nebo pauza
≥ 0,6 s. Věta delší než 12 s se rozdělí u čárky nebo nejdelší pauzy blízko
středu. Každé slovo dostane mluvčího podle největšího překryvu s jeho úsekem;
věta se rozdělí, kde se mluvčí mění, a krátké přeskoky (šum diarizace) se připojí
k sousedovi. Velmi krátké věty téhož mluvčího se slepí, aby TTS mělo kontext.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import CIL_VYPLNENI, MAX_POZICKA_S, REZERVA_ZA_VETOU_S, ZNAKU_ZA_S, ZNAKU_ZA_S_JINAK
from .job import Veta, Zakazka

MIN_BEH_SLOV = 2
MIN_BEH_S = 0.4
KRATKA_VETA_S = 1.0
MAX_SLEPENA_S = 12.0
MAX_MEZERA_SLEPENI_S = 0.4
CISTOTA_OKOLI_S = 0.3
PAUZA_S = 0.6
MIN_KONEC_VETY_S = 1.5
MAX_VETA_S = 12.0
MIN_DIL_S = 2.0
KONCE_VET = tuple(".?!…")
KONCE_VYPOVEDI = tuple(",;:—–")


def _jednotky(slova: list[list]) -> list[list[list]]:
    """Věty podle interpunkce a pauz; dlouhé se dál dělí v `_rozdel`."""
    jednotky: list[list[list]] = []
    aktualni: list[list] = []
    for i, s in enumerate(slova):
        aktualni.append(s)
        dalsi = slova[i + 1] if i + 1 < len(slova) else None
        mezera = dalsi[1] - s[2] if dalsi else float("inf")
        delka = s[2] - aktualni[0][1]
        if dalsi is None or mezera >= PAUZA_S or (s[0].endswith(KONCE_VET) and delka >= MIN_KONEC_VETY_S):
            jednotky.append(aktualni)
            aktualni = []
    vysledek: list[list[list]] = []
    for j in jednotky:
        vysledek.extend(_rozdel(j))
    return vysledek


def _rozdel(slova: list[list]) -> list[list[list]]:
    if slova[-1][2] - slova[0][1] <= MAX_VETA_S or len(slova) < 4:
        return [slova]
    nejlepsi: tuple[float, int] | None = None
    for i in range(1, len(slova)):
        vlevo = slova[i - 1][2] - slova[0][1]
        vpravo = slova[-1][2] - slova[i][1]
        if vlevo < MIN_DIL_S or vpravo < MIN_DIL_S:
            continue
        skore = abs(vlevo - vpravo)
        if slova[i - 1][0].endswith(KONCE_VYPOVEDI):
            skore -= 3.0
        if slova[i - 1][0].endswith(KONCE_VET):
            skore -= 5.0
        skore -= min(slova[i][1] - slova[i - 1][2], 1.0) * 4.0
        if nejlepsi is None or skore < nejlepsi[0]:
            nejlepsi = (skore, i)
    i = nejlepsi[1] if nejlepsi else len(slova) // 2
    return _rozdel(slova[:i]) + _rozdel(slova[i:])


def _mluvci_slova(od: float, do: float, useky: list[dict]) -> int:
    if not useky:
        return 0
    nejlepsi, prekryv = None, 0.0
    for u in useky:
        p = min(do, u["end"]) - max(od, u["start"])
        if p > prekryv:
            nejlepsi, prekryv = u["mluvci"], p
    if nejlepsi is not None:
        return nejlepsi
    stred = (od + do) / 2
    return min(useky, key=lambda u: min(abs(u["start"] - stred), abs(u["end"] - stred)))["mluvci"]


def _behy(slova: list[list], mluvci: list[int]) -> list[tuple[int, list[list]]]:
    behy: list[list] = []
    for s, m in zip(slova, mluvci):
        if behy and behy[-1][0] == m:
            behy[-1][1].append(s)
        else:
            behy.append([m, [s]])
    # krátké přeskoky připojit k předchozímu (nebo následujícímu) běhu
    zmena = True
    while zmena and len(behy) > 1:
        zmena = False
        for i, (m, sl) in enumerate(behy):
            delka = sl[-1][2] - sl[0][1]
            if len(sl) < MIN_BEH_SLOV or delka < MIN_BEH_S:
                j = i - 1 if i > 0 else i + 1
                if j > i:
                    behy[j][1] = sl + behy[j][1]
                else:
                    behy[j][1] = behy[j][1] + sl
                del behy[i]
                zmena = True
                break
    # sousední běhy téhož mluvčího (po spojení) sloučit
    vysledek: list[list] = []
    for m, sl in behy:
        if vysledek and vysledek[-1][0] == m:
            vysledek[-1][1].extend(sl)
        else:
            vysledek.append([m, sl])
    return [(m, sl) for m, sl in vysledek]


def _text(slova: list[list]) -> str:
    return " ".join(s[0] for s in slova).strip()


def sestav(zakazka: Zakazka) -> list[Veta]:
    slozka = zakazka.slozka
    asr = json.loads((slozka / "asr.json").read_text(encoding="utf-8"))["slova"]
    diar_cesta = slozka / "diar.json"
    useky = json.loads(diar_cesta.read_text(encoding="utf-8")) if diar_cesta.exists() else []
    if zakazka.pocet_mluvcich == 1:
        useky = []

    kusy: list[tuple[int, list[list]]] = []
    for slova in _jednotky(asr):
        mluvci = [_mluvci_slova(s[1], s[2], useky) for s in slova]
        kusy.extend(_behy(slova, mluvci))

    # krátké věty téhož mluvčího slepit s následující
    slepene: list[tuple[int, list[list]]] = []
    for m, sl in kusy:
        if slepene:
            pm, psl = slepene[-1]
            mezera = sl[0][1] - psl[-1][2]
            kratka = (psl[-1][2] - psl[0][1] < KRATKA_VETA_S) or (sl[-1][2] - sl[0][1] < KRATKA_VETA_S)
            if (pm == m and kratka and mezera <= MAX_MEZERA_SLEPENI_S
                    and sl[-1][2] - psl[0][1] <= MAX_SLEPENA_S):
                slepene[-1] = (pm, psl + sl)
                continue
        slepene.append((m, list(sl)))

    vety: list[Veta] = []
    for i, (m, sl) in enumerate(slepene, 1):
        vety.append(Veta(id=i, start=round(sl[0][1], 3), end=round(sl[-1][2], 3), mluvci=m,
                         en=_text(sl), slova=sl))
    prepocitej_limity(zakazka, vety)
    for v in vety:
        cizi = [u for u in useky if u["mluvci"] != v.mluvci
                and u["end"] > v.start - CISTOTA_OKOLI_S and u["start"] < v.end + CISTOTA_OKOLI_S]
        v.cista = not cizi
    return vety


def uloz_vety(zakazka: Zakazka) -> None:
    zakazka.vety = sestav(zakazka)
    zakazka.hotovo("vety")


if __name__ == "__main__":
    import sys
    z = Zakazka.nacti(Path(sys.argv[1]))
    uloz_vety(z)
    print(f"{len(z.vety)} vět, mluvčí: {sorted({v.mluvci for v in z.vety})}")


def prepocitej_limity(zakazka: Zakazka, vety: list[Veta] | None = None) -> None:
    """Cíl a strop znaků překladu podle délky věty a rychlosti řeči hlasu v cílovém jazyce
    (změřené z kotvy mluvčího, jinak odhad z config.ZNAKU_ZA_S). Volá se před překladem."""
    vety = zakazka.vety if vety is None else vety
    vychozi = ZNAKU_ZA_S.get(zakazka.jazyk, ZNAKU_ZA_S_JINAK)
    for i, v in enumerate(vety):
        # změřeno z kotvy: jak rychle mluví právě tenhle hlas v cílovém jazyce (tts.py)
        znaku_za_s = zakazka.rychlost_hlasu.get(str(v.mluvci)) or vychozi
        dalsi = vety[i + 1].start if i + 1 < len(vety) else zakazka.delka_s or v.end + MAX_POZICKA_S
        pozicka = max(0.0, min(dalsi - v.end - REZERVA_ZA_VETOU_S, MAX_POZICKA_S))
        v.max_znaku = max(8, int((v.delka + pozicka) * znaku_za_s))
        v.cil_znaku = max(6, int(v.delka * znaku_za_s * CIL_VYPLNENI))
