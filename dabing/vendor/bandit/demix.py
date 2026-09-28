# Převzato z ZFTurbo/Music-Source-Separation-Training (MIT, viz LICENSE), commit
# 84b1eac0887756b4f1a9d7a1ff49105939749ed2, utils/model_utils.py (demix, _getWindowingArray,
# prefer_target_instrument)
# https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/utils/model_utils.py
#
# Jen „generic“ větev (Demucs větev nepotřebujeme). Dělení na úseky, reflect padding
# okrajů, okno s lineárním náběhem/doběhem a jeho výjimky pro první/poslední úsek jsou
# beze změny (včetně toho, že výjimka okna platí pro celou dávku, jako upstream).
# Úpravy: config je obyčejný dict z yaml, místo tqdm volitelné `hlaseni(podil)`,
# akumulace vždy v CPU paměti (upstream ji dává na GPU jen u CUDA), žádné torch.compile
# ani CUDA autocast (na CPU/MPS nic nedělá).

from typing import Callable, Dict, Optional

import numpy as np
import torch
from torch import nn


def _getWindowingArray(window_size: int, fade_size: int) -> torch.Tensor:
    """Okno: lineární náběh 0→1 přes fade_size vzorků, jedničky, doběh 1→0."""
    fadein = torch.linspace(0, 1, fade_size)
    fadeout = torch.linspace(1, 0, fade_size)

    window = torch.ones(window_size)
    window[-fade_size:] = fadeout
    window[:fade_size] = fadein
    return window


def prefer_target_instrument(config: dict) -> list[str]:
    cil = config["training"].get("target_instrument")
    return [cil] if cil else list(config["training"]["instruments"])


def demix(
    config: dict,
    model: torch.nn.Module,
    mix: np.ndarray,
    device: torch.device | str,
    hlaseni: Optional[Callable[[float], None]] = None,
) -> Dict[str, np.ndarray]:
    """Rozdělí mix (kanály, vzorky) na stopy {nástroj: (kanály, vzorky)} po úsecích."""
    mix = torch.tensor(mix, dtype=torch.float32)
    instruments = prefer_target_instrument(config)
    num_instruments = len(instruments)

    acc_device = torch.device("cpu")
    mix = mix.to(acc_device)

    inference = config.get("inference", {})
    if "chunk_size" in inference:
        chunk_size = inference["chunk_size"]
    else:
        chunk_size = config["audio"]["chunk_size"]
    num_overlap = inference["num_overlap"]

    fade_size = chunk_size // 10
    step = chunk_size // num_overlap
    border = chunk_size - step
    length_init = mix.shape[-1]
    windowing_array = _getWindowingArray(chunk_size, fade_size).to(acc_device)
    # Add padding for generic mode to handle edge artifacts
    if length_init > 2 * border and border > 0:
        mix = nn.functional.pad(mix, (border, border), mode="reflect")

    batch_size = inference.get("batch_size", 1)

    with torch.inference_mode():
        # Initialize result and counter tensors (counter is the same for all instruments and channels)
        req_shape = (num_instruments,) + mix.shape
        result = torch.zeros(req_shape, dtype=torch.float32, device=acc_device)
        counter = torch.zeros(mix.shape[-1], dtype=torch.float32, device=acc_device)

        i = 0
        batch_data = []
        batch_locations = []

        while i < mix.shape[1]:
            # Extract chunk and apply padding if necessary
            part = mix[:, i:i + chunk_size].to(device)
            chunk_len = part.shape[-1]
            if chunk_len > chunk_size // 2:
                pad_mode = "reflect"
            else:
                pad_mode = "constant"
            part = nn.functional.pad(part, (0, chunk_size - chunk_len), mode=pad_mode, value=0)

            batch_data.append(part)
            batch_locations.append((i, chunk_len))
            i += step

            # Process batch if it's full or the end is reached
            if len(batch_data) >= batch_size or i >= mix.shape[1]:
                arr = torch.stack(batch_data, dim=0)
                x = model(arr).to(acc_device, torch.float32)

                window = windowing_array.clone()
                if i - step == 0:  # First audio chunk, no fadein
                    window[:fade_size] = 1
                elif i >= mix.shape[1]:  # Last audio chunk, no fadeout
                    window[-fade_size:] = 1

                for j, (start, seg_len) in enumerate(batch_locations):
                    result[..., start:start + seg_len] += x[j, ..., :seg_len] * window[..., :seg_len]
                    counter[start:start + seg_len] += window[..., :seg_len]

                batch_data.clear()
                batch_locations.clear()

                if hlaseni is not None:
                    hlaseni(min(i / mix.shape[1], 1.0))

        # Compute final estimated sources
        estimated_sources = result.div_(counter)

        # Remove padding for generic mode
        if length_init > 2 * border and border > 0:
            estimated_sources = estimated_sources[..., border:-border]

        estimated_sources = estimated_sources.cpu().numpy()
        np.nan_to_num(estimated_sources, copy=False, nan=0.0)

    return {k: v for k, v in zip(instruments, estimated_sources)}
