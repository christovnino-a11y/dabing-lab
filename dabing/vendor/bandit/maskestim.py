# Původně BandIt (Karn N. Watcharasupat a kol., https://github.com/kwatcharasupat/bandit, Apache-2.0,
# viz LICENSE-APACHE); MSST ho převzal do models/bandit bez licence.
# Převzato z ZFTurbo/Music-Source-Separation-Training (MIT, viz LICENSE), commit
# 84b1eac0887756b4f1a9d7a1ff49105939749ed2, models/bandit/core/model/bsrnn/maskestim.py
# https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/models/bandit/core/model/bsrnn/maskestim.py
#
# Úpravy:
# - NormMLP skládá hidden/output jako obyčejné nn.Sequential místo torch.jit.script(...).
#   Klíče state_dict jsou stejné (hidden.0.*, output.0.*), výpočet taky; TorchScript je
#   v novém torchi zastaralý a na MPS nic nepřidá.
# - Vyhozeny MultAddNormMLP, MaskEstimationModule (nepřekrývající se pásma) a podmínka
#   `cond` (config má cond_dim=0, mult_add_mask=false, pásma se překrývají).
# - freq_weights jsou v checkpointu float64 (upstream je tak uložil); výpočet je nechává
#   jak jsou (maska sama zůstává complex64), na MPS je stage před přesunem převede
#   na float32, protože MPS float64 neumí.

from typing import Dict, List, Optional, Tuple, Type

import torch
from torch import nn
from torch.nn.modules import activation

from dabing.vendor.bandit.utils import (
    band_widths_from_specs,
    check_no_gap,
    check_nonzero_bandwidth,
)


class BaseNormMLP(nn.Module):
    def __init__(
            self,
            emb_dim: int,
            mlp_dim: int,
            bandwidth: int,
            in_channel: Optional[int],
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs=None,
            complex_mask: bool = True, ):

        super().__init__()
        if hidden_activation_kwargs is None:
            hidden_activation_kwargs = {}
        self.hidden_activation_kwargs = hidden_activation_kwargs
        self.norm = nn.LayerNorm(emb_dim)
        self.hidden = nn.Sequential(
                nn.Linear(in_features=emb_dim, out_features=mlp_dim),
                activation.__dict__[hidden_activation](
                        **self.hidden_activation_kwargs
                ),
        )

        self.bandwidth = bandwidth
        self.in_channel = in_channel

        self.complex_mask = complex_mask
        self.reim = 2 if complex_mask else 1
        self.glu_mult = 2


class NormMLP(BaseNormMLP):
    def __init__(
            self,
            emb_dim: int,
            mlp_dim: int,
            bandwidth: int,
            in_channel: Optional[int],
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs=None,
            complex_mask: bool = True,
    ) -> None:
        super().__init__(
                emb_dim=emb_dim,
                mlp_dim=mlp_dim,
                bandwidth=bandwidth,
                in_channel=in_channel,
                hidden_activation=hidden_activation,
                hidden_activation_kwargs=hidden_activation_kwargs,
                complex_mask=complex_mask,
        )

        self.output = nn.Sequential(
                nn.Linear(
                        in_features=mlp_dim,
                        out_features=bandwidth * in_channel * self.reim * 2,
                ),
                nn.GLU(dim=-1),
        )

    def reshape_output(self, mb):
        batch, n_time, _ = mb.shape
        if self.complex_mask:
            mb = mb.reshape(
                    batch,
                    n_time,
                    self.in_channel,
                    self.bandwidth,
                    self.reim
            ).contiguous()
            mb = torch.view_as_complex(
                    mb
            )  # (batch, n_time, in_channel, bandwidth)
        else:
            mb = mb.reshape(batch, n_time, self.in_channel, self.bandwidth)

        mb = torch.permute(
                mb,
                (0, 2, 3, 1)
        )  # (batch, in_channel, bandwidth, n_time)

        return mb

    def forward(self, qb):
        # qb = (batch, n_time, emb_dim)
        qb = self.norm(qb)  # (batch, n_time, emb_dim)
        qb = self.hidden(qb)  # (batch, n_time, mlp_dim)
        mb = self.output(qb)  # (batch, n_time, bandwidth * in_channel * reim)
        mb = self.reshape_output(mb)  # (batch, in_channel, bandwidth, n_time)

        return mb


class MaskEstimationModuleSuperBase(nn.Module):
    pass


class MaskEstimationModuleBase(MaskEstimationModuleSuperBase):
    def __init__(
            self,
            band_specs: List[Tuple[float, float]],
            emb_dim: int,
            mlp_dim: int,
            in_channel: Optional[int],
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs: Dict = None,
            complex_mask: bool = True,
            norm_mlp_cls: Type[nn.Module] = NormMLP,
            norm_mlp_kwargs: Dict = None,
    ) -> None:
        super().__init__()

        self.band_widths = band_widths_from_specs(band_specs)
        self.n_bands = len(band_specs)

        if hidden_activation_kwargs is None:
            hidden_activation_kwargs = {}

        if norm_mlp_kwargs is None:
            norm_mlp_kwargs = {}

        self.norm_mlp = nn.ModuleList(
                [
                        (
                                norm_mlp_cls(
                                        bandwidth=self.band_widths[b],
                                        emb_dim=emb_dim,
                                        mlp_dim=mlp_dim,
                                        in_channel=in_channel,
                                        hidden_activation=hidden_activation,
                                        hidden_activation_kwargs=hidden_activation_kwargs,
                                        complex_mask=complex_mask,
                                        **norm_mlp_kwargs,
                                )
                        )
                        for b in range(self.n_bands)
                ]
        )

    def compute_masks(self, q):
        batch, n_bands, n_time, emb_dim = q.shape

        masks = []

        for b, nmlp in enumerate(self.norm_mlp):
            qb = q[:, b, :, :]
            mb = nmlp(qb)
            masks.append(mb)

        return masks


class OverlappingMaskEstimationModule(MaskEstimationModuleBase):
    def __init__(
            self,
            in_channel: int,
            band_specs: List[Tuple[float, float]],
            freq_weights: List[torch.Tensor],
            n_freq: int,
            emb_dim: int,
            mlp_dim: int,
            cond_dim: int = 0,
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs: Dict = None,
            complex_mask: bool = True,
            norm_mlp_cls: Type[nn.Module] = NormMLP,
            norm_mlp_kwargs: Dict = None,
            use_freq_weights: bool = True,
    ) -> None:
        check_nonzero_bandwidth(band_specs)
        check_no_gap(band_specs)

        if cond_dim > 0:
            raise NotImplementedError("podmíněná varianta (cond_dim > 0) se nepřevzala")

        super().__init__(
                band_specs=band_specs,
                emb_dim=emb_dim + cond_dim,
                mlp_dim=mlp_dim,
                in_channel=in_channel,
                hidden_activation=hidden_activation,
                hidden_activation_kwargs=hidden_activation_kwargs,
                complex_mask=complex_mask,
                norm_mlp_cls=norm_mlp_cls,
                norm_mlp_kwargs=norm_mlp_kwargs,
        )

        self.n_freq = n_freq
        self.band_specs = band_specs
        self.in_channel = in_channel

        if freq_weights is not None:
            for i, fw in enumerate(freq_weights):
                self.register_buffer(f"freq_weights/{i}", fw)

                self.use_freq_weights = use_freq_weights
        else:
            self.use_freq_weights = False

        self.cond_dim = cond_dim

    def forward(self, q, cond=None):
        # q = (batch, n_bands, n_time, emb_dim)
        if cond is not None:
            raise NotImplementedError("podmíněná varianta (cond) se nepřevzala")

        batch, n_bands, n_time, emb_dim = q.shape

        mask_list = self.compute_masks(
                q
        )  # [n_bands  * (batch, in_channel, bandwidth, n_time)]

        masks = torch.zeros(
                (batch, self.in_channel, self.n_freq, n_time),
                device=q.device,
                dtype=mask_list[0].dtype,
        )

        for im, mask in enumerate(mask_list):
            fstart, fend = self.band_specs[im]
            if self.use_freq_weights:
                fw = self.get_buffer(f"freq_weights/{im}")[:, None]
                # s float64 fw vyjde součin complex128, += ho vrátí do complex64 masky
                mask = mask * fw
            masks[:, :, fstart:fend, :] += mask

        return masks
