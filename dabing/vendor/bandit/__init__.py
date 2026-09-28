"""BandIt Plus (BSRNN, dialog/hudba/efekty) — jen část potřebná pro inferenci.

Původně BandIt (Karn N. Watcharasupat a kol., https://github.com/kwatcharasupat/bandit,
licence Apache-2.0, viz LICENSE-APACHE). Převzato přes
https://github.com/ZFTurbo/Music-Source-Separation-Training (licence MIT, viz LICENSE),
který kód zkopíroval do models/bandit/ bez licence, a utils/model_utils.py na commitu
ZDROJ_COMMIT. Config yaml (MIT) je zkopírovaný vedle, ať projde čistým klonem. Vyhozeno: Lightning, trénink, loss, metriky, data, ostatní varianty modelu.
torchaudio a librosa nahrazeny ekvivalenty v torch/numpy (viz _spectral.py a utils.py).

Soubory drží upstreamová jména, aby šly porovnat s originálem:
  wrapper.py  ← models/bandit/core/model/bsrnn/wrapper.py (MultiMaskMultiSourceBandSplitRNNSimple)
  core.py     ← models/bandit/core/model/bsrnn/core.py + bsrnn/__init__.py
  bandsplit.py, tfmodel.py, maskestim.py, utils.py ← models/bandit/core/model/bsrnn/
  _spectral.py ← models/bandit/core/model/_spectral.py
  demix.py    ← utils/model_utils.py (demix, _getWindowingArray)
"""

ZDROJ_REPO = "https://github.com/ZFTurbo/Music-Source-Separation-Training"
ZDROJ_COMMIT = "84b1eac0887756b4f1a9d7a1ff49105939749ed2"

from dabing.vendor.bandit.wrapper import (  # noqa: E402
    MultiMaskMultiSourceBandSplitRNNSimple,
    model_z_configu,
)
from dabing.vendor.bandit.demix import demix  # noqa: E402

__all__ = ["MultiMaskMultiSourceBandSplitRNNSimple", "model_z_configu", "demix",
           "ZDROJ_REPO", "ZDROJ_COMMIT"]
