"""Krok 3: kdo kdy mluví (NVIDIA Nemotron-3-Diarization přes transformers na MPS).

Převzato ze stt-labu (`_diarizuj`, `_diarizuj_proudove`). Do ~5 min jde celá
nahrávka najednou (nejpřesnější), delší proudově po kouscích s pamětí mluvčích.
Mluvčí jsou číslovaní podle toho, kdo promluvil první.

Vstup:  vocals16.wav
Výstup: diar.json [{"start", "end", "mluvci"}]
"""

from __future__ import annotations

import json

import numpy as np
import torch
from transformers import AutoModelForAudioFrameClassification, AutoProcessor

from dabing.config import MODEL_DIAR
from dabing.stages._spolecne import nacti_mono, prubeh, slozka_z_argv

SR = 16000
OFFLINE_MAX_S = 300
ZARIZENI = "mps" if torch.backends.mps.is_available() else "cpu"


def _proudove(processor, model, zvuk: np.ndarray) -> torch.Tensor:
    """Postup podle karty modelu (sekce Streaming diarization)."""
    processor.set_streaming_mode("low_latency")

    def vstupy():
        yield processor(zvuk[: processor.num_samples_first_audio_chunk], sampling_rate=SR,
                        is_streaming=True, is_first_audio_chunk=True)
        mel = processor.num_mel_frames_per_step
        start = processor.audio_chunk_start(mel)
        while (konec := start + processor.num_samples_per_audio_chunk) <= zvuk.shape[0]:
            yield processor(zvuk[start:konec], sampling_rate=SR,
                            is_streaming=True, is_first_audio_chunk=False)
            mel += processor.num_mel_frames_per_step
            start = processor.audio_chunk_start(mel)
        yield processor(zvuk[start:], sampling_rate=SR, is_streaming=True,
                        is_first_audio_chunk=False, is_last_audio_chunk=True)

    cache, logits, krok = None, [], 0
    celkem = max(1, zvuk.shape[0] // max(processor.num_samples_per_audio_chunk, 1))
    for inputs in vstupy():
        inputs = inputs.to(ZARIZENI, dtype=model.dtype)
        vystup = model(**inputs, speaker_cache=cache)
        logits.append(vystup.logits)
        cache = vystup.speaker_cache
        krok += 1
        if krok % 20 == 0:
            prubeh(0.1 + 0.85 * min(krok / celkem, 1.0), "Rozlišuji mluvčí…")
    return torch.cat(logits, dim=1)


def main() -> None:
    slozka = slozka_z_argv()
    cil = slozka / "diar.json"
    if cil.exists():
        prubeh(1.0, "Mluvčí už jsou rozlišení")
        return
    prubeh(0.0, "Načítám Nemotron…")
    processor = AutoProcessor.from_pretrained(MODEL_DIAR)
    model = AutoModelForAudioFrameClassification.from_pretrained(MODEL_DIAR, dtype=torch.float32)
    model = model.to(ZARIZENI)
    model.train(False)

    zvuk, _ = nacti_mono(slozka / "vocals16.wav", SR)
    prubeh(0.1, "Rozlišuji mluvčí…")
    with torch.inference_mode():
        if len(zvuk) <= OFFLINE_MAX_S * SR:
            inputs = processor(zvuk, sampling_rate=SR).to(ZARIZENI, dtype=model.dtype)
            logits = model(**inputs).logits
            segmenty = processor.extract_speaker_dict(logits, inputs.get("attention_mask"))[0]
        else:
            segmenty = processor.extract_speaker_dict(_proudove(processor, model, zvuk))[0]
    useky = [{"start": round(float(s["Start"]), 3), "end": round(float(s["End"]), 3),
              "mluvci": int(s["Speaker"])} for s in segmenty]
    useky.sort(key=lambda u: u["start"])
    cil.write_text(json.dumps(useky, ensure_ascii=False, indent=1), encoding="utf-8")
    pocet = len({u["mluvci"] for u in useky})
    prubeh(1.0, f"Rozlišeno {pocet} mluvčích")


if __name__ == "__main__":
    main()
