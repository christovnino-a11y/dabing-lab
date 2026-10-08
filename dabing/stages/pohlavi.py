"""Pohlaví mluvčích z výšky hlasu — kvůli rodu v překladu (české „řekl jsem" × „řekla jsem").

Překladatel text slyší jen jako text a rod by hádal. Hlas, který dabing naklonuje, ale
pohlaví má, takže se změří: medián základní frekvence (F0) z čistých vět každého mluvčího
(Demucs hlas, 16 kHz). Mužský hlas v řeči má F0 kolem 85–155 Hz, ženský kolem 165–255 Hz;
mezi 150 a 165 Hz je pásmo, kde se údaj nepoužije („?") a překladatel rod určí z kontextu.

Vstup:  job.json (věty s mluvčím), vocals16.wav
Výstup: pohlavi.json {mluvci: {"pohlavi": "m"|"z"|"?", "f0": Hz, "vteriny": délka vzorku}}
"""

from __future__ import annotations

import json

import numpy as np

from dabing.stages._spolecne import nacti_mono, prubeh, slozka_z_argv

SR = 16000
MAX_VZOREK_S = 45.0       # víc zvuku mluvčího nepomůže a pyin je pomalý
MIN_VETA_S = 1.0
MIN_RAMCU = 30            # méně hlasových rámců = údaj nelze vzít vážně
HRANICE_MUZ_HZ = 150.0
HRANICE_ZENA_HZ = 165.0
PRAVDEPODOBNOST_HLASU = 0.4


def urci(f0_hz: float | None) -> str:
    if f0_hz is None:
        return "?"
    if f0_hz <= HRANICE_MUZ_HZ:
        return "m"
    if f0_hz >= HRANICE_ZENA_HZ:
        return "z"
    return "?"


def median_f0(x: np.ndarray) -> tuple[float | None, int]:
    """Medián F0 z hlasových rámců (pyin). Když pyin hlas skoro nenajde (chraplavý nebo tichý
    hlas), záložně yin jen z rámců s dost energií — méně přesný, ale vždy něco vrátí."""
    import librosa
    f0, hlas, pravdepodobnost = librosa.pyin(x, fmin=60, fmax=300, sr=SR, frame_length=2048)
    f = f0[hlas & (pravdepodobnost > PRAVDEPODOBNOST_HLASU)]
    if len(f) >= MIN_RAMCU:
        return float(np.median(f)), int(len(f))
    f0 = librosa.yin(x, fmin=60, fmax=300, sr=SR, frame_length=2048)
    energie = librosa.feature.rms(y=x, frame_length=2048, hop_length=512)[0]
    n = min(len(f0), len(energie))
    f = f0[:n][energie[:n] > 0.5 * np.median(energie[:n][energie[:n] > 0.05 * energie.max()])]
    return (float(np.median(f)), int(len(f))) if len(f) >= MIN_RAMCU else (None, int(len(f)))


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
        kusy, celkem = [], 0.0
        for v in vety:
            if celkem >= MAX_VZOREK_S:
                break
            kusy.append(hlas[int(v["start"] * sr):int(v["end"] * sr)])
            celkem += v["end"] - v["start"]
        f0, ramcu = median_f0(np.concatenate(kusy)) if kusy else (None, 0)
        vysledek[str(m)] = {"pohlavi": urci(f0), "f0": None if f0 is None else round(f0, 1),
                            "ramcu": ramcu, "vteriny": round(celkem, 1)}
    (slozka / "pohlavi.json").write_text(json.dumps(vysledek, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    prubeh(1.0, "Pohlaví mluvčích změřeno")


if __name__ == "__main__":
    main()
