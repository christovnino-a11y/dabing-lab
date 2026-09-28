# Původně BandIt (Karn N. Watcharasupat a kol., https://github.com/kwatcharasupat/bandit, Apache-2.0,
# viz LICENSE-APACHE); MSST ho převzal do models/bandit bez licence.
# Převzato z ZFTurbo/Music-Source-Separation-Training (MIT, viz LICENSE), commit
# 84b1eac0887756b4f1a9d7a1ff49105939749ed2, models/bandit/core/model/bsrnn/utils.py
# https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/models/bandit/core/model/bsrnn/utils.py
#
# Ponecháno jen „musical“ dělení pásem (config dnr_bandit_bsrnn_multi_mus64).
# Úprava: upstream bere hz_to_midi / midi_to_hz z librosa. Jsou to jednořádkové vzorce
# (librosa: midi = 12·(log2 f − log2 440) + 69, f = 440·2^((midi − 69)/12)), kvůli nim
# nemá stage tahat librosu (pomalý import, numba). Ověřeno 2026-09-28 proti librosa 1.0.0
# a doslovně spuštěnému upstream musical_filterbank: filterbank, band_specs i freq_weights
# bitově shodné a freq_weights shodné s buffery v checkpointu.

from abc import abstractmethod
from typing import Callable

import numpy as np
import torch


def hz_to_midi(frekvence):
    """Ekvivalent librosa.hz_to_midi."""
    return 12 * (np.log2(np.asanyarray(frekvence)) - np.log2(440.0)) + 69


def midi_to_hz(noty):
    """Ekvivalent librosa.midi_to_hz."""
    return 440.0 * (2.0 ** ((np.asanyarray(noty) - 69.0) / 12.0))


def band_widths_from_specs(band_specs):
    return [e - i for i, e in band_specs]


def check_nonzero_bandwidth(band_specs):
    for fstart, fend in band_specs:
        if fend - fstart <= 0:
            raise ValueError("Bands cannot be zero-width")


def check_no_overlap(band_specs):
    fend_prev = -1
    for fstart_curr, fend_curr in band_specs:
        if fstart_curr <= fend_prev:
            raise ValueError("Bands cannot overlap")


def check_no_gap(band_specs):
    fstart, _ = band_specs[0]
    assert fstart == 0

    fend_prev = -1
    for fstart_curr, fend_curr in band_specs:
        if fstart_curr - fend_prev > 1:
            raise ValueError("Bands cannot leave gap")
        fend_prev = fend_curr


class BandsplitSpecification:
    def __init__(self, nfft: int, fs: int) -> None:
        self.fs = fs
        self.nfft = nfft
        self.nyquist = fs / 2
        self.max_index = nfft // 2 + 1

    @abstractmethod
    def get_band_specs(self):
        raise NotImplementedError


class PerceptualBandsplitSpecification(BandsplitSpecification):
    def __init__(
            self,
            nfft: int,
            fs: int,
            fbank_fn: Callable[[int, int, float, float, int], torch.Tensor],
            n_bands: int,
            f_min: float = 0.0,
            f_max: float = None
    ) -> None:
        super().__init__(nfft=nfft, fs=fs)
        self.n_bands = n_bands
        if f_max is None:
            f_max = fs / 2

        self.filterbank = fbank_fn(
                n_bands, fs, f_min, f_max, self.max_index
        )

        weight_per_bin = torch.sum(
            self.filterbank,
            dim=0,
            keepdim=True
            )  # (1, n_freqs)
        normalized_mel_fb = self.filterbank / weight_per_bin  # (n_mels, n_freqs)

        freq_weights = []
        band_specs = []
        for i in range(self.n_bands):
            active_bins = torch.nonzero(self.filterbank[i, :]).squeeze().tolist()
            if isinstance(active_bins, int):
                active_bins = (active_bins, active_bins)
            if len(active_bins) == 0:
                continue
            start_index = active_bins[0]
            end_index = active_bins[-1] + 1
            band_specs.append((start_index, end_index))
            freq_weights.append(normalized_mel_fb[i, start_index:end_index])

        self.freq_weights = freq_weights
        self.band_specs = band_specs

    def get_band_specs(self):
        return self.band_specs

    def get_freq_weights(self):
        return self.freq_weights


def musical_filterbank(n_bands, fs, f_min, f_max, n_freqs,
                       scale="constant"):

    nfft = 2 * (n_freqs - 1)
    df = fs / nfft
    # init freqs
    f_max = f_max or fs / 2
    f_min = f_min or 0
    f_min = fs / nfft

    n_octaves = np.log2(f_max / f_min)
    n_octaves_per_band = n_octaves / n_bands
    bandwidth_mult = np.power(2.0, n_octaves_per_band)

    low_midi = max(0, hz_to_midi(f_min))
    high_midi = hz_to_midi(f_max)
    midi_points = np.linspace(low_midi, high_midi, n_bands)
    hz_pts = midi_to_hz(midi_points)

    low_pts = hz_pts / bandwidth_mult
    high_pts = hz_pts * bandwidth_mult

    low_bins = np.floor(low_pts / df).astype(int)
    high_bins = np.ceil(high_pts / df).astype(int)

    fb = np.zeros((n_bands, n_freqs))

    for i in range(n_bands):
        fb[i, low_bins[i]:high_bins[i]+1] = 1.0

    fb[0, :low_bins[0]] = 1.0
    fb[-1, high_bins[-1]+1:] = 1.0

    return torch.as_tensor(fb)


class MusicalBandsplitSpecification(PerceptualBandsplitSpecification):
    def __init__(
            self,
            nfft: int,
            fs: int,
            n_bands: int,
            f_min: float = 0.0,
            f_max: float = None
    ) -> None:
        super().__init__(fbank_fn=musical_filterbank, nfft=nfft, fs=fs, n_bands=n_bands, f_min=f_min, f_max=f_max)
