# Původně BandIt (Karn N. Watcharasupat a kol., https://github.com/kwatcharasupat/bandit, Apache-2.0,
# viz LICENSE-APACHE); MSST ho převzal do models/bandit bez licence.
# Převzato z ZFTurbo/Music-Source-Separation-Training (MIT, viz LICENSE), commit
# 84b1eac0887756b4f1a9d7a1ff49105939749ed2, models/bandit/core/model/_spectral.py
# https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/models/bandit/core/model/_spectral.py
#
# Úprava: upstream staví STFT z torchaudio.transforms.Spectrogram/InverseSpectrogram.
# torchaudio v projektu není a pro power=None, pad=0 jde jen o tenký obal nad
# torch.stft/torch.istft. Náhrada níže opakuje vzorec torchaudio.functional
# (spectrogram / inverse_spectrogram): normalized=True znamená normalizaci oknem,
# tj. torch.stft(normalized=False) / sqrt(sum(okno²)) a u inverze naopak ×sqrt(sum(okno²)).
# Okno je buffer „window“ jako v torchaudio, takže klíče stft.window / istft.window
# v checkpointu sedí (load_state_dict strict=True).

from typing import Dict, Optional

import torch
from torch import nn


def _normy(normalized) -> tuple[bool, bool]:
    """(frame_length_norm, window_norm) přesně jako torchaudio _get_spec_norms."""
    if isinstance(normalized, str):
        if normalized not in ("frame_length", "window"):
            raise ValueError(f"neznámá normalizace: {normalized}")
        return normalized == "frame_length", normalized == "window"
    return False, bool(normalized)


class Spektrogram(nn.Module):
    """Komplexní STFT (power=None) jako torchaudio.transforms.Spectrogram."""

    def __init__(self, n_fft, win_length, hop_length, window_fn, wkwargs, normalized, center,
                 pad_mode, onesided) -> None:
        super().__init__()
        self.n_fft = n_fft
        self.win_length = win_length if win_length is not None else n_fft
        self.hop_length = hop_length if hop_length is not None else self.win_length // 2
        okno = window_fn(self.win_length) if wkwargs is None else window_fn(self.win_length, **wkwargs)
        self.register_buffer("window", okno)
        self.frame_length_norm, self.window_norm = _normy(normalized)
        self.center = center
        self.pad_mode = pad_mode
        self.onesided = onesided

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        tvar = x.shape
        x = x.reshape(-1, tvar[-1])
        spec = torch.stft(x, n_fft=self.n_fft, hop_length=self.hop_length, win_length=self.win_length,
                          window=self.window, center=self.center, pad_mode=self.pad_mode,
                          normalized=self.frame_length_norm, onesided=self.onesided, return_complex=True)
        spec = spec.reshape(tvar[:-1] + spec.shape[-2:])
        if self.window_norm:
            spec = spec / self.window.pow(2.0).sum().sqrt()
        return spec


class InverzniSpektrogram(nn.Module):
    """Inverzní STFT jako torchaudio.transforms.InverseSpectrogram (pad=0)."""

    def __init__(self, n_fft, win_length, hop_length, window_fn, wkwargs, normalized, center,
                 pad_mode, onesided) -> None:
        super().__init__()
        self.n_fft = n_fft
        self.win_length = win_length if win_length is not None else n_fft
        self.hop_length = hop_length if hop_length is not None else self.win_length // 2
        okno = window_fn(self.win_length) if wkwargs is None else window_fn(self.win_length, **wkwargs)
        self.register_buffer("window", okno)
        self.frame_length_norm, self.window_norm = _normy(normalized)
        self.center = center
        self.onesided = onesided

    def forward(self, spec: torch.Tensor, length: Optional[int] = None) -> torch.Tensor:
        if self.window_norm:
            spec = spec * self.window.pow(2.0).sum().sqrt()
        tvar = spec.shape
        spec = spec.reshape(-1, tvar[-2], tvar[-1])
        x = torch.istft(spec, n_fft=self.n_fft, hop_length=self.hop_length, win_length=self.win_length,
                        window=self.window, center=self.center, normalized=self.frame_length_norm,
                        onesided=self.onesided, length=length, return_complex=False)
        return x.reshape(tvar[:-2] + x.shape[-1:])


class _SpectralComponent(nn.Module):
    def __init__(
            self,
            n_fft: int = 2048,
            win_length: Optional[int] = 2048,
            hop_length: int = 512,
            window_fn: str = "hann_window",
            wkwargs: Optional[Dict] = None,
            power: Optional[int] = None,
            center: bool = True,
            normalized: bool = True,
            pad_mode: str = "constant",
            onesided: bool = True,
            **kwargs,
    ) -> None:
        super().__init__()

        assert power is None

        window_fn = torch.__dict__[window_fn]
        spolecne = dict(n_fft=n_fft, win_length=win_length, hop_length=hop_length, window_fn=window_fn,
                        wkwargs=wkwargs, normalized=normalized, center=center, pad_mode=pad_mode,
                        onesided=onesided)
        self.stft = Spektrogram(**spolecne)
        self.istft = InverzniSpektrogram(**spolecne)
