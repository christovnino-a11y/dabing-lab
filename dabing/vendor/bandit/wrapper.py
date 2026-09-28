# Původně BandIt (Karn N. Watcharasupat a kol., https://github.com/kwatcharasupat/bandit, Apache-2.0,
# viz LICENSE-APACHE); MSST ho převzal do models/bandit bez licence.
# Převzato z ZFTurbo/Music-Source-Separation-Training (MIT, viz LICENSE), commit
# 84b1eac0887756b4f1a9d7a1ff49105939749ed2, models/bandit/core/model/bsrnn/wrapper.py
# https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/models/bandit/core/model/bsrnn/wrapper.py
#
# Úpravy:
# - BandSplitWrapperBase dědí z nn.Module místo pytorch_lightning.LightningModule
#   (Lightning slouží jen tréninku; state_dict klíče se nemění).
# - get_band_specs umí jen „musical“ (jediné, co config používá).
# - Ponechána jen třída MultiMaskMultiSourceBandSplitRNNSimple, kterou MSST staví pro
#   model_type 'bandit' (utils/settings.py: MultiMaskMultiSourceBandSplitRNNSimple(**config.model)).
# - Přidáno model_z_configu(): totéž co MSST get_model_from_config pro 'bandit', bez ml_collections.

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import torch
import yaml
from torch import nn

from dabing.vendor.bandit._spectral import _SpectralComponent
from dabing.vendor.bandit.core import MultiSourceMultiMaskBandSplitCoreRNN
from dabing.vendor.bandit.utils import MusicalBandsplitSpecification


def get_band_specs(band_specs, n_fft, fs, n_bands=None):
    if "musical" in band_specs:
        assert n_bands is not None
        specs = MusicalBandsplitSpecification(
                nfft=n_fft,
                fs=fs,
                n_bands=n_bands
        )
        bsm = specs.get_band_specs()
        freq_weights = specs.get_freq_weights()
        overlapping_band = True
    else:
        raise NameError(f"převzato jen dělení pásem 'musical', ne {band_specs!r}")

    return bsm, freq_weights, overlapping_band


class BandSplitWrapperBase(nn.Module):
    bsrnn: nn.Module

    def __init__(self, **kwargs):
        super().__init__()


class MultiMaskMultiSourceBandSplitBaseSimple(
        BandSplitWrapperBase,
        _SpectralComponent
):
    def __init__(
            self,
            stems: List[str],
            band_specs: Union[str, List[Tuple[float, float]]],
            fs: int = 44100,
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
            n_bands: int = None,
    ) -> None:
        super().__init__(
                n_fft=n_fft,
                win_length=win_length,
                hop_length=hop_length,
                window_fn=window_fn,
                wkwargs=wkwargs,
                power=power,
                center=center,
                normalized=normalized,
                pad_mode=pad_mode,
                onesided=onesided,
        )

        if isinstance(band_specs, str):
            self.band_specs, self.freq_weights, self.overlapping_band = get_band_specs(
                band_specs,
                n_fft,
                fs,
                n_bands
                )

        self.stems = stems

    def forward(self, batch):
        # batch = (batch, kanály, vzorky) → (batch, stopy, kanály, vzorky)
        with torch.no_grad():
            X = self.stft(batch)
        length = batch.shape[-1]
        output = self.bsrnn(X, cond=None)
        res = []
        for stem, S in output["spectrogram"].items():
            s = self.istft(S, length)
            res.append(s)
        res = torch.stack(res, dim=1)
        return res


class MultiMaskMultiSourceBandSplitRNNSimple(MultiMaskMultiSourceBandSplitBaseSimple):
    def __init__(
            self,
            in_channel: int,
            stems: List[str],
            band_specs: Union[str, List[Tuple[float, float]]],
            fs: int = 44100,
            require_no_overlap: bool = False,
            require_no_gap: bool = True,
            normalize_channel_independently: bool = False,
            treat_channel_as_feature: bool = True,
            n_sqm_modules: int = 12,
            emb_dim: int = 128,
            rnn_dim: int = 256,
            cond_dim: int = 0,
            bidirectional: bool = True,
            rnn_type: str = "LSTM",
            mlp_dim: int = 512,
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs: Optional[Dict] = None,
            complex_mask: bool = True,
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
            n_bands: int = None,
            use_freq_weights: bool = True,
            normalize_input: bool = False,
            mult_add_mask: bool = False,
            freeze_encoder: bool = False,
    ) -> None:
        super().__init__(
                stems=stems,
                band_specs=band_specs,
                fs=fs,
                n_fft=n_fft,
                win_length=win_length,
                hop_length=hop_length,
                window_fn=window_fn,
                wkwargs=wkwargs,
                power=power,
                center=center,
                normalized=normalized,
                pad_mode=pad_mode,
                onesided=onesided,
                n_bands=n_bands,
        )

        self.bsrnn = MultiSourceMultiMaskBandSplitCoreRNN(
                stems=stems,
                band_specs=self.band_specs,
                in_channel=in_channel,
                require_no_overlap=require_no_overlap,
                require_no_gap=require_no_gap,
                normalize_channel_independently=normalize_channel_independently,
                treat_channel_as_feature=treat_channel_as_feature,
                n_sqm_modules=n_sqm_modules,
                emb_dim=emb_dim,
                rnn_dim=rnn_dim,
                bidirectional=bidirectional,
                rnn_type=rnn_type,
                mlp_dim=mlp_dim,
                cond_dim=cond_dim,
                hidden_activation=hidden_activation,
                hidden_activation_kwargs=hidden_activation_kwargs,
                complex_mask=complex_mask,
                overlapping_band=self.overlapping_band,
                freq_weights=self.freq_weights,
                n_freq=n_fft // 2 + 1,
                use_freq_weights=use_freq_weights,
                mult_add_mask=mult_add_mask
        )

        self.normalize_input = normalize_input
        self.cond_dim = cond_dim

        if freeze_encoder:
            for param in self.bsrnn.band_split.parameters():
                param.requires_grad = False

            for param in self.bsrnn.tf_model.parameters():
                param.requires_grad = False


def model_z_configu(cesta_yaml: str | Path) -> tuple[MultiMaskMultiSourceBandSplitRNNSimple, dict]:
    """Postaví model z yaml configu MSST (bez vah) a vrátí (model, config)."""
    config = yaml.safe_load(Path(cesta_yaml).read_text())
    model = MultiMaskMultiSourceBandSplitRNNSimple(**config["model"])
    return model, config
