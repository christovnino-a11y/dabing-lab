"""Krok: oddělení dialogu od hudby a efektů (BandIt Plus, trénovaný na DnR).

Použití: python -m dabing.stages.dialog <složka> [original|hlas] [--vse | --efekty]

Vstup:  "original" = audio_hq.wav (výchozí), "hlas" = vocals.wav z Demucs
Výstup: rec_<vstup>.wav (stopa speech, 44,1 kHz stereo float); s --vse navíc
        hudba_<vstup>.wav a efekty_<vstup>.wav; s --efekty jen efekty_<vstup>.wav
        (to používá dabing: z hlasové stopy Demucsu vrátí jásot a smích do pozadí),
        dialog_<vstup>.json {model, commit, verze, vstup, stopy, zarizeni, cas_s, …}
        (krok se přeskočí, když json sedí s modelem, commitem a verzí, obsahuje
        požadované stopy a výstupy jsou novější než vstup)

Demucs dává do vocals i jásot, smích a zpěv; BandIt je trénovaný na řeč vs. hudbu vs.
efekty, takže publikum by mělo skončit v efektech. Model má in_channel 1: stereo se
uvnitř přeskládá na dva mono průchody (kanál = položka dávky), vstup se nenormalizuje.
Dělení na 6s úseky s překryvem 4×, lineární okno a reflect padding okrajů je přesně
upstream demix z MSST (dabing/vendor/bandit/demix.py).

Zařízení: zkušební průchod jedním úsekem (6 s) dvakrát na CPU i MPS; MPS jen když
sedí s CPU a zahřátý je aspoň o 20 % rychlejší. DABING_DIALOG_CPU=1 vynutí procesor
(kdyby grafika padala tvrdě, bez výjimky). Když model kdekoli vrátí NaN/Inf,
krok to nepřejde potichu (ticho ve stopě řeči by nechalo pod češtinou angličtinu):
na MPS přepočítá na CPU, na CPU skončí chybou.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import time

import numpy as np
import soundfile as sf
import soxr
import torch

from dabing.config import DIALOG_VERZE, MODEL_DIALOG_CHECKPOINT, MODEL_DIALOG_CONFIG, MODEL_DIALOG_SHA256
from dabing.stages._spolecne import prubeh, slozka_z_argv
from dabing.vendor.bandit import ZDROJ_COMMIT, demix, model_z_configu

VSTUPY = {"original": "audio_hq.wav", "hlas": "vocals.wav"}
STOPY_SOUBORY = {"speech": "rec", "music": "hudba", "effects": "efekty"}
PRAH_SHODY_MPS_DB = 60.0    # odstup MPS od CPU na zkušebním průchodu (horší → CPU)
MPS_RYCHLEJSI = 0.8         # MPS jen když zahřátý průchod trvá nejvýš 80 % CPU


class NefinitniVystup(RuntimeError):
    pass


class _Hlidac(torch.nn.Module):
    """Obal modelu: každý úsek zkontroluje na NaN/Inf (upstream demix je tiše nuluje)."""

    def __init__(self, model: torch.nn.Module):
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.model(x)
        if not torch.isfinite(y).all():
            raise NefinitniVystup("BandIt vrátil NaN/Inf")
        return y


def _zapis(cesta, data: np.ndarray, sr: int) -> None:
    docasny = cesta.with_name(cesta.name + ".tmp")
    sf.write(docasny, np.ascontiguousarray(data), sr, subtype="FLOAT", format="WAV")
    os.replace(docasny, cesta)


def _zapis_json(cesta, data: dict) -> None:
    docasny = cesta.with_name(cesta.name + ".tmp")
    docasny.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(docasny, cesta)


def _over_checkpoint() -> None:
    if not MODEL_DIALOG_CHECKPOINT.exists():
        raise SystemExit(f"Chybí model BandIt: {MODEL_DIALOG_CHECKPOINT}")
    h = hashlib.sha256()
    with open(MODEL_DIALOG_CHECKPOINT, "rb") as f:
        for blok in iter(lambda: f.read(1 << 22), b""):
            h.update(blok)
    if h.hexdigest() != MODEL_DIALOG_SHA256:
        raise SystemExit("Soubor modelu BandIt se liší od ověřeného (SHA-256) — nepoužívám ho.")


def nacti_model() -> tuple[torch.nn.Module, dict]:
    """Model z yaml + váhy z checkpointu (jen tenzory, weights_only=True), na CPU."""
    _over_checkpoint()
    model, config = model_z_configu(MODEL_DIALOG_CONFIG)
    if config.get("inference", {}).get("normalize"):
        raise NotImplementedError("config s inference.normalize převzatý demix neumí")
    vahy = torch.load(MODEL_DIALOG_CHECKPOINT, map_location="cpu", weights_only=True)
    model.load_state_dict(vahy, strict=True)
    model.train(False)  # inferenční režim (totéž co .eval())
    return model, config


def _float32(model: torch.nn.Module) -> torch.nn.Module:
    """freq_weights jsou v checkpointu float64; MPS float64 neumí."""
    for modul in model.modules():
        for jmeno, buf in modul.named_buffers(recurse=False):
            if buf.dtype == torch.float64:
                modul._buffers[jmeno] = buf.float()
    return model


def _odstup_db(ref: torch.Tensor, x: torch.Tensor) -> float:
    chyba = float(((ref - x) ** 2).sum())
    return float("inf") if chyba == 0 else 10 * np.log10(float((ref ** 2).sum()) / chyba + 1e-30)


def _dvakrat(model: torch.nn.Module, x: torch.Tensor, mps: bool) -> tuple[torch.Tensor, float]:
    """Dva průchody; vrací výstup a čas druhého (zahřátého)."""
    with torch.inference_mode():
        for _ in range(2):
            t = time.monotonic()
            y = model(x)
            if mps:
                torch.mps.synchronize()
            cas = time.monotonic() - t
    return (y.cpu() if mps else y), cas


def vyber_zarizeni(model: torch.nn.Module, config: dict
                   ) -> tuple[torch.nn.Module, str, dict]:
    """Zkušební průchod jedním úsekem šumu na CPU a MPS; vrátí (model, zařízení, měření)."""
    if os.environ.get("DABING_DIALOG_CPU") == "1":
        return model, "cpu", {"mps": "vypnuto (DABING_DIALOG_CPU=1)"}
    if not torch.backends.mps.is_available():
        return model, "cpu", {"mps": "nedostupné"}
    gen = torch.Generator().manual_seed(0)
    x = torch.randn(1, 2, int(config["audio"]["chunk_size"]), generator=gen) * 0.1
    y_cpu, cas_cpu = _dvakrat(model, x, mps=False)
    info: dict = {"cpu_s": round(cas_cpu, 3)}
    if not torch.isfinite(y_cpu).all():
        raise NefinitniVystup("BandIt vrátil NaN/Inf už na CPU")
    try:
        model_mps = _float32(copy.deepcopy(model)).to("mps")
        y_mps, cas_mps = _dvakrat(model_mps, x.to("mps"), mps=True)
    except Exception as e:  # MPS neumí některou operaci → CPU
        info["mps"] = f"chyba: {type(e).__name__}: {e}"[:300]
        return model, "cpu", info
    info["mps_s"] = round(cas_mps, 3)
    if not torch.isfinite(y_mps).all():
        info["mps"] = "NaN/Inf ve výstupu"
        return model, "cpu", info
    odstup = min(_odstup_db(y_cpu[:, i], y_mps[:, i]) for i in range(y_cpu.shape[1]))
    info["odstup_mps_db"] = round(odstup, 1) if math.isfinite(odstup) else None
    if not math.isfinite(odstup) or odstup < PRAH_SHODY_MPS_DB or cas_mps > MPS_RYCHLEJSI * cas_cpu:
        return model, "cpu", info
    return model_mps, "mps", info


def _argumenty() -> tuple[str, list[str]]:
    volne = [a for a in sys.argv[2:] if not a.startswith("--")]
    vstup = volne[0] if volne else "original"
    if vstup not in VSTUPY:
        raise SystemExit(f"vstup musí být jeden z {sorted(VSTUPY)}, ne {vstup!r}")
    if "--vse" in sys.argv[2:]:
        return vstup, ["speech", "music", "effects"]
    return vstup, ["effects"] if "--efekty" in sys.argv[2:] else ["speech"]


def _hotovo(info_cesta, vystupy: dict, stopy: list[str], vstup_cesta) -> bool:
    if not info_cesta.exists() or not all(c.exists() for c in vystupy.values()):
        return False
    try:
        info = json.loads(info_cesta.read_text(encoding="utf-8"))
        stopy_hotove = set(info.get("stopy") or [])
    except (ValueError, TypeError, AttributeError):
        return False
    cerstve = all(c.stat().st_mtime >= vstup_cesta.stat().st_mtime for c in vystupy.values())
    return (cerstve and info.get("model") == MODEL_DIALOG_CHECKPOINT.name
            and info.get("commit") == ZDROJ_COMMIT and info.get("verze") == DIALOG_VERZE
            and set(stopy) <= stopy_hotove)


def main() -> None:
    slozka = slozka_z_argv()
    vstup, stopy = _argumenty()
    vstup_cesta = slozka / VSTUPY[vstup]
    info_cesta = slozka / f"dialog_{vstup}.json"
    vystupy = {s: slozka / f"{STOPY_SOUBORY[s]}_{vstup}.wav" for s in stopy}
    if not vstup_cesta.exists():
        raise SystemExit(f"Chybí {vstup_cesta.name} — nejdřív musí proběhnout separace.")
    if _hotovo(info_cesta, vystupy, stopy, vstup_cesta):
        prubeh(1.0, "Dialog už je oddělený")
        return
    for stary in [info_cesta, info_cesta.with_name(info_cesta.name + ".tmp"),
                  *slozka.glob(f"*_{vstup}.wav.tmp")]:
        stary.unlink(missing_ok=True)
    zacatek = time.monotonic()

    prubeh(0.0, "Načítám BandIt…")
    model, config = nacti_model()
    sr_modelu = int(config["audio"]["sample_rate"])

    wav, sr = sf.read(vstup_cesta, dtype="float32", always_2d=True)
    if sr != sr_modelu:
        wav = soxr.resample(wav, sr, sr_modelu, quality="HQ").astype(np.float32)
        sr = sr_modelu
    if wav.shape[1] == 1:  # upstream: mono se zdvojí na stereo (config num_channels 2)
        wav = np.concatenate([wav, wav], axis=1)
    vstup_audio = np.ascontiguousarray(wav[:, :2].T)  # (kanály, vzorky); víc kanálů se nebere
    del wav

    prubeh(0.02, "Zkouším, jestli je rychlejší grafika, nebo procesor…")
    model_cpu = model
    model, zarizeni, mereni = vyber_zarizeni(model, config)
    print(f"zařízení {zarizeni}: {mereni}", flush=True)

    def hlaseni(podil: float) -> None:
        prubeh(0.05 + 0.9 * podil, "Odděluji dialog od hudby a efektů…")

    selhani = ""
    try:
        zdroje = demix(config, _Hlidac(model), vstup_audio, zarizeni, hlaseni=hlaseni)
    except RuntimeError as chyba:  # včetně NefinitniVystup
        if zarizeni != "mps":
            raise
        selhani = f"{type(chyba).__name__}: {chyba}"[:300]
    if selhani:
        # přepočet až mimo except: traceback by jinak držel v paměti rozpracovaný běh
        mereni["mps_selhal"] = selhani
        del model
        torch.mps.empty_cache()
        zarizeni = "cpu"
        prubeh(0.05, "Grafika selhala, počítám znovu na procesoru…")
        zdroje = demix(config, _Hlidac(model_cpu), vstup_audio, zarizeni,
                       hlaseni=lambda podil: prubeh(0.05 + 0.9 * podil, "Odděluji dialog (procesor)…"))

    for stopa, cesta in vystupy.items():
        _zapis(cesta, zdroje[stopa].T, sr)
    energie = float(np.mean(vstup_audio ** 2)) + 1e-20
    zbytek = vstup_audio - sum(zdroje.values())
    _zapis_json(info_cesta, {
        "model": MODEL_DIALOG_CHECKPOINT.name, "commit": ZDROJ_COMMIT, "verze": DIALOG_VERZE,
        "vstup": vstup, "stopy": stopy, "zarizeni": zarizeni,
        "cas_s": round(time.monotonic() - zacatek, 1),
        "delka_s": round(vstup_audio.shape[1] / sr, 2),
        "energie_stop_db": {s: round(10 * np.log10(float(np.mean(v ** 2)) / energie + 1e-20), 1)
                            for s, v in zdroje.items()},
        "nezarazeno_db": round(10 * np.log10(float(np.mean(zbytek ** 2)) / energie + 1e-20), 1),
        "zkouska_zarizeni": mereni,
    })
    prubeh(1.0, "Dialog oddělený")


if __name__ == "__main__":
    main()
