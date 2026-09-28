"""Krok 1: oddělení hlasu od pozadí (Demucs htdemucs z lokální HF cache).

Vstup:  audio_hq.wav (44,1 kHz stereo)
Výstup: vocals.wav (44,1 kHz stereo), vocals16.wav (16 kHz mono pro přepis),
        separace.json (model; krok se přeskočí, když sedí s config.MODEL_SEPARACE)

Pozadí se neukládá: mix ho počítá jako originál − g·hlas (viz dabing/maska.py).
Soubory se zapisují přes dočasné jméno, takže zrušený krok nenechá půlku stopy.

`hf://htdemucs` je jediná cesta bez tichého stahování z internetu: bez prefixu
demucs 4.1.0 chybu HF spolkne a sáhne na dl.fbaipublicfiles.com.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import soundfile as sf
import soxr
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

from dabing.config import MODEL_SEPARACE, SEPARACE_VERZE
from dabing.stages._spolecne import prubeh, slozka_z_argv


def _zapis(cesta, data: np.ndarray, sr: int) -> None:
    docasny = cesta.with_name(cesta.name + ".tmp")
    sf.write(docasny, data, sr, subtype="FLOAT", format="WAV")
    os.replace(docasny, cesta)


def main() -> None:
    slozka = slozka_z_argv()
    vystup_hlas = slozka / "vocals.wav"
    if vystup_hlas.exists() and (slozka / "vocals16.wav").exists():
        info = slozka / "separace.json"
        # starší zakázky separace.json nemají — ty dělal tentýž Demucs, stopy platí dál
        model = json.loads(info.read_text())["model"] if info.exists() else MODEL_SEPARACE
        if model == MODEL_SEPARACE:
            prubeh(1.0, "Hlas a pozadí už jsou oddělené")
            return
    zacatek = time.monotonic()

    prubeh(0.0, "Načítám Demucs…")
    model = get_model(MODEL_SEPARACE)
    model.eval()
    zarizeni = "mps" if torch.backends.mps.is_available() else "cpu"

    wav, sr = sf.read(slozka / "audio_hq.wav", dtype="float32", always_2d=True)
    if sr != model.samplerate:
        wav = soxr.resample(wav, sr, model.samplerate, quality="HQ").astype(np.float32)
        sr = model.samplerate
    x = torch.from_numpy(np.ascontiguousarray(wav.T))
    ref = x.mean(0)
    stred, rozptyl = ref.mean(), ref.std() + 1e-8
    x = (x - stred) / rozptyl

    delka = x.shape[-1]

    def hlaseni(info: dict) -> None:
        if info.get("state") == "end":
            hotovo = (info.get("segment_offset", 0) + info.get("segment_length", 0)) / max(delka, 1)
            prubeh(min(hotovo, 0.99), "Odděluji hlas od hudby a ruchů…")

    with torch.no_grad():
        zdroje = apply_model(model, x[None], device=zarizeni, shifts=1, split=True, overlap=0.25,
                             progress=False, num_workers=0, callback=hlaseni)[0]
    zdroje = zdroje * rozptyl + stred
    hlas = zdroje[model.sources.index("vocals")].numpy().astype(np.float32)
    vstup = np.ascontiguousarray(wav.T)
    zbytek = vstup - zdroje.sum(0).numpy()

    _zapis(vystup_hlas, hlas.T, sr)
    _zapis(slozka / "vocals16.wav", soxr.resample(hlas.mean(0), sr, 16000, quality="HQ"), 16000)
    (slozka / "no_vocals.wav").unlink(missing_ok=True)  # starší verze; mix ho už nečte
    energie = float(np.mean(vstup ** 2)) + 1e-20
    (slozka / "separace.json").write_text(json.dumps({
        "model": MODEL_SEPARACE, "verze": SEPARACE_VERZE,
        "nezarazeno_db": round(10 * np.log10(float(np.mean(zbytek ** 2)) / energie + 1e-20), 1),
        "cas_s": round(time.monotonic() - zacatek, 1),
    }, ensure_ascii=False, indent=1))
    prubeh(1.0, "Hlas a pozadí oddělené")


if __name__ == "__main__":
    main()
