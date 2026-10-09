"""Pohlaví mluvčích z výšky hlasu — kvůli rodu v překladu (české „řekl jsem" × „řekla jsem").

Překladatel text slyší jen jako text a rod by hádal. Hlas, který dabing naklonuje, ale
pohlaví má, takže se změří: základní frekvence (F0) po jednotlivých větách z Demucs hlasu
(16 kHz) a z ní medián za mluvčího. Mužský hlas v řeči má F0 kolem 85–155 Hz, ženský kolem
165–255 Hz; mezi 150 a 165 Hz je pásmo, kde se údaj nepoužije („?") a překladatel rod určí
z kontextu. Hlas nad 250 Hz je u dospělých vzácný (dítě, nebo velmi vysoký ženský hlas) a u dětí
se chlapec od dívky z hlasu nepozná — i to je „?" (pipeline ho pak může ověřit na snímcích).

Měření po větách odhalí i SMÍCHANÉHO mluvčího: diarizace má strop počtu mluvčích a v dlouhém
videu s mnoha lidmi slije muže a ženu do jednoho (Gemini at Work: M8 měl 25 vět na 100–130 Hz
a 40 na 190–250 Hz — jedna reference, jeden klon, ženský hlas přes muže). Když má mluvčí dvě
oddělené skupiny (muži / ženy, mezera mezi nimi), vypíše se seznam vět každé skupiny
(`smisene`) a pipeline je rozdělí na dva mluvčí.

Vstup:  job.json (věty s mluvčím), vocals16.wav
Výstup: pohlavi.json {mluvci: {"pohlavi": "m"|"z"|"?", "f0": Hz, "vety": {id: Hz},
                               "smisene": {"m": [id…], "z": [id…]} (jen u smíchaného)}}
"""

from __future__ import annotations

import json

import numpy as np

from dabing.stages._spolecne import nacti_mono, prubeh, slozka_z_argv

SR = 16000
MAX_VET = 600             # nejvýš tolik vět na mluvčího (nejdelší čisté); měření trvá desítky sekund
MIN_VETA_S = 1.2
MIN_RAMCU = 30            # méně hlasových rámců za mluvčího = údaj nelze vzít vážně
MIN_RAMCU_VETA = 10       # totéž za jednu větu
HRANICE_MUZ_HZ = 150.0
HRANICE_ZENA_HZ = 165.0
HRANICE_VYSOKY_HZ = 250.0     # nad tím nelze určit podle hlasu (dítě)
PRAVDEPODOBNOST_HLASU = 0.4
# Smíchaný mluvčí: věta je „mužská" pod MUZ_VETA_HZ, „ženská" od ZENA_VETA_HZ; pásmo mezi nimi
# musí být skoro prázdné (náhodný oktávový skok by věty rozházel i tam) a obě skupiny dost velké.
MUZ_VETA_HZ = 140.0
ZENA_VETA_HZ = 175.0
MIN_SKUPINA = 6
MIN_PODIL = 0.12
MAX_PODIL_PASMA = 0.15
MIN_ROZDIL_MEDIANU_HZ = 60.0


def urci(f0_hz: float | None) -> str:
    if f0_hz is None:
        return "?"
    if f0_hz <= HRANICE_MUZ_HZ:
        return "m"
    if f0_hz >= HRANICE_VYSOKY_HZ:
        return "?"
    if f0_hz >= HRANICE_ZENA_HZ:
        return "z"
    return "?"


def median_f0(x: np.ndarray, min_ramcu: int = MIN_RAMCU) -> tuple[float | None, int]:
    """Medián F0 z hlasových rámců (pyin). Když pyin hlas skoro nenajde (chraplavý nebo tichý
    hlas), záložně yin jen z rámců s dost energií — méně přesný, ale vždy něco vrátí."""
    import librosa
    f0, hlas, pravdepodobnost = librosa.pyin(x, fmin=60, fmax=300, sr=SR, frame_length=2048)
    f = f0[hlas & (pravdepodobnost > PRAVDEPODOBNOST_HLASU)]
    if len(f) >= min_ramcu:
        return float(np.median(f)), int(len(f))
    f0 = librosa.yin(x, fmin=60, fmax=300, sr=SR, frame_length=2048)
    energie = librosa.feature.rms(y=x, frame_length=2048, hop_length=512)[0]
    n = min(len(f0), len(energie))
    prah = 0.5 * np.median(energie[:n][energie[:n] > 0.05 * energie.max()])
    f = f0[:n][energie[:n] > prah]
    return (float(np.median(f)), int(len(f))) if len(f) >= min_ramcu else (None, int(len(f)))


def smisene(f0_vet: dict[str, float]) -> dict[str, list[int]] | None:
    """Dvě oddělené skupiny (muži, ženy) v jednom mluvčím → {"m": [id…], "z": [id…]}, jinak None."""
    if len(f0_vet) < 2 * MIN_SKUPINA:
        return None
    muzi = [k for k, f in f0_vet.items() if f < MUZ_VETA_HZ]
    zeny = [k for k, f in f0_vet.items() if f >= ZENA_VETA_HZ]
    pasmo = len(f0_vet) - len(muzi) - len(zeny)
    n = len(f0_vet)
    if (min(len(muzi), len(zeny)) < MIN_SKUPINA or min(len(muzi), len(zeny)) / n < MIN_PODIL
            or pasmo / n > MAX_PODIL_PASMA):
        return None
    if np.median([f0_vet[k] for k in zeny]) - np.median([f0_vet[k] for k in muzi]) < MIN_ROZDIL_MEDIANU_HZ:
        return None
    return {"m": sorted(int(k) for k in muzi), "z": sorted(int(k) for k in zeny)}


def main() -> None:
    slozka = slozka_z_argv()
    zakazka = json.loads((slozka / "job.json").read_text(encoding="utf-8"))
    hlas, sr = nacti_mono(slozka / "vocals16.wav", SR)
    mluvci = sorted({v["mluvci"] for v in zakazka["vety"]})
    vysledek = {}
    for i, m in enumerate(mluvci):
        prubeh(i / len(mluvci), f"Zjišťuji pohlaví mluvčího M{m + 1}…")
        vety = [v for v in zakazka["vety"] if v["mluvci"] == m and v["end"] - v["start"] >= MIN_VETA_S]
        # nejdřív věty bez cizího hlasu v okolí, v nich nejdelší
        vety.sort(key=lambda v: (not v.get("cista", False), -(v["end"] - v["start"])))
        f0_vet: dict[str, float] = {}
        for v in vety[:MAX_VET]:
            f0, _ = median_f0(hlas[int(v["start"] * sr):int(v["end"] * sr)], MIN_RAMCU_VETA)
            if f0 is not None:
                f0_vet[str(v["id"])] = round(f0, 1)
        f0 = float(np.median(list(f0_vet.values()))) if len(f0_vet) >= 3 else None
        zaznam = {"pohlavi": urci(f0), "f0": None if f0 is None else round(f0, 1),
                  "vety": f0_vet, "ramcu": len(f0_vet)}
        rozdeleni = smisene(f0_vet)
        if rozdeleni:
            zaznam["smisene"] = rozdeleni
        vysledek[str(m)] = zaznam
    (slozka / "pohlavi.json").write_text(json.dumps(vysledek, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    prubeh(1.0, "Pohlaví mluvčích změřeno")


if __name__ == "__main__":
    main()
