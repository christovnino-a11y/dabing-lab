"""OmniVoice worker pro Dabing lab — běží ve vlastním venv a vlastním procesu.

Proč zvlášť: chyba MPS alokátoru shodí celý proces bez Python výjimky a zaseknuté
nativní volání nejde přerušit. Lab proto worker jen restartuje a pokračuje.

Protokol: jeden JSON na řádek, stdin → stdout. Výpisy knihoven jdou na stderr,
stdout patří jen odpovědím.
  {"op": "nacti"}                                   → {"ok": true, "zarizeni": "mps"}
  {"op": "synth", "id", "text", "jazyk", "ref_audio", "ref_text",
   "duration"?, "kroky"?, "guidance"?, "denoise"?, "seed"?, "orez"?, "vystup"}
                                                    → {"ok": true, "id", "sekundy", "vypocet_s"}
  {"op": "identita", "a": wav, "b": wav}            → {"ok": true, "podobnost": 0.73}
  {"op": "konec"}
Chyba: {"ok": false, "chyba": "…"}.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

# Odpovědi jen do původního stdout; vše ostatní (tisky knihoven) na stderr.
_PROTOKOL = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402
import torch  # noqa: E402

MODEL = "k2-fsa/OmniVoice"
ECAPA = "speechbrain/spkrec-ecapa-voxceleb"
SR = 24000
ZARIZENI = "mps" if torch.backends.mps.is_available() else "cpu"

_model = None
_prompty: dict[tuple[str, float, str], object] = {}
_ecapa = None
_vektory: dict[tuple[str, float], torch.Tensor] = {}


def _odpovez(data: dict) -> None:
    _PROTOKOL.write(json.dumps(data, ensure_ascii=False) + "\n")
    _PROTOKOL.flush()


def _nacti_model():
    global _model
    if _model is None:
        from omnivoice import OmniVoice
        _model = OmniVoice.from_pretrained(MODEL, device_map=ZARIZENI, dtype=torch.float16)
    return _model


def _nacti_zvuk(cesta: str, sr: int) -> np.ndarray:
    x, puvodni = sf.read(cesta, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if puvodni != sr:
        x = soxr.resample(x, puvodni, sr, quality="HQ").astype(np.float32)
    return x


def _prompt(ref_audio: str, ref_text: str):
    """Kódování reference je drahé — stejná reference se použije pro mnoho vět."""
    klic = (ref_audio, os.path.getmtime(ref_audio), ref_text)
    if klic not in _prompty:
        model = _nacti_model()
        vlna = torch.from_numpy(_nacti_zvuk(ref_audio, SR))[None]
        _prompty[klic] = model.create_voice_clone_prompt(ref_audio=(vlna, SR), ref_text=ref_text)
        if len(_prompty) > 64:
            _prompty.pop(next(iter(_prompty)))
    return _prompty[klic]


def synth(zadani: dict) -> dict:
    model = _nacti_model()
    start = time.monotonic()
    parametry = {
        "num_step": int(zadani.get("kroky", 32)),
        "guidance_scale": float(zadani.get("guidance", 2.0)),
        "denoise": bool(zadani.get("denoise", True)),
        # vlastní ořez ticha OmniVoice má pevný práh −50 dBFS a tiše vyslovené jednoslovné věty
        # („alebo") uřízne celé; s orez=False ořízne ticho až lab (práh vůči špičce věty)
        "postprocess_output": bool(zadani.get("orez", True)),
    }
    if zadani.get("seed") is not None:
        torch.manual_seed(int(zadani["seed"]))
    audio = model.generate(
        text=zadani["text"],
        language=zadani.get("jazyk") or None,
        voice_clone_prompt=_prompt(zadani["ref_audio"], zadani.get("ref_text", "")),
        duration=zadani.get("duration"),
        **parametry,
    )[0]
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    sf.write(zadani["vystup"], audio, SR, subtype="FLOAT")
    if ZARIZENI == "mps":
        torch.mps.empty_cache()
    return {"ok": True, "id": zadani.get("id"), "sekundy": round(len(audio) / SR, 3),
            "vypocet_s": round(time.monotonic() - start, 2)}


def _vektor(cesta: str) -> torch.Tensor:
    global _ecapa
    klic = (cesta, os.path.getmtime(cesta))
    if klic not in _vektory:
        if _ecapa is None:
            from speechbrain.inference.speaker import EncoderClassifier
            _ecapa = EncoderClassifier.from_hparams(
                source=ECAPA,
                savedir=os.path.join(os.path.dirname(__file__), "ecapa"),
                run_opts={"device": "cpu"},
            )
        vlna = torch.from_numpy(_nacti_zvuk(cesta, 16000))[None]
        _vektory[klic] = _ecapa.encode_batch(vlna).reshape(-1)
        if len(_vektory) > 512:
            _vektory.pop(next(iter(_vektory)))
    return _vektory[klic]


def identita(zadani: dict) -> dict:
    a, b = _vektor(zadani["a"]), _vektor(zadani["b"])
    podobnost = torch.nn.functional.cosine_similarity(a, b, dim=0).item()
    return {"ok": True, "podobnost": round(float(podobnost), 4)}


def main() -> None:
    _odpovez({"ok": True, "pripraven": True, "zarizeni": ZARIZENI})
    for radek in sys.stdin:
        radek = radek.strip()
        if not radek:
            continue
        try:
            zadani = json.loads(radek)
            op = zadani.get("op")
            if op == "konec":
                _odpovez({"ok": True})
                return
            if op == "nacti":
                _nacti_model()
                _odpovez({"ok": True, "zarizeni": ZARIZENI})
            elif op == "synth":
                _odpovez(synth(zadani))
            elif op == "identita":
                _odpovez(identita(zadani))
            else:
                _odpovez({"ok": False, "chyba": f"neznámá operace {op!r}"})
        except Exception as chyba:  # noqa: BLE001 — chyba jedné věty nesmí ukončit worker
            traceback.print_exc()
            _odpovez({"ok": False, "chyba": f"{type(chyba).__name__}: {chyba}"})


if __name__ == "__main__":
    main()
