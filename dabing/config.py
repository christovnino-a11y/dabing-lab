"""Společné cesty a konstanty Dabing labu."""

from __future__ import annotations

import os
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
JOBS = LAB / "jobs"
LOGY = LAB / "logy"
PYTHON = LAB / ".venv" / "bin" / "python"

# OmniVoice běží ve vlastním venv a odděleném procesu: chyba MPS alokátoru
# shodí celý proces bez Python výjimky (stejný důvod má i VoiceStudio).
WORKER_PYTHON = LAB / "worker" / "omnivoice" / ".venv" / "bin" / "python"
WORKER_SKRIPT = LAB / "worker" / "omnivoice" / "omni_worker.py"

PORT = 7872

# Modely — všechny už leží v ~/.cache/huggingface/hub, nic se nestahuje.
MODEL_ASR = "mlx-community/parakeet-tdt-0.6b-v3"
MODEL_DIAR = "nvidia/Nemotron-3-Diarization"
MODEL_SEPARACE = "hf://htdemucs"  # jen s prefixem hf:// se nenačítá z internetu
SEPARACE_VERZE = 2                # 2 = pozadí je přesný doplněk (vstup − hlas)
MODEL_TTS = "k2-fsa/OmniVoice"
MODEL_ECAPA = "speechbrain/spkrec-ecapa-voxceleb"

# Kalibrace délky: jak rychle mluví OmniVoice (změřeno 2026-09-27 na 20 větách:
# čeština medián 16,2 znaku/s přirozeně). Ostatní jazyky odhadem.
ZNAKU_ZA_S = {"cs": 16.0, "sk": 16.0, "de": 15.5, "en": 15.5}
# Překlad má vyplnit zhruba tolik času, kolik mluví originál (řečník na obraze
# mluví — dlouhé ticho po krátké české větě působí jako chyba).
CIL_VYPLNENI = 0.9

JAZYKY = {"Čeština": "cs", "Slovenština": "sk", "Němčina": "de", "Angličtina": "en"}

# Jazyk originálu: Parakeet ho pozná sám (25 evropských jazyků), údaj slouží překladači.
JAZYKY_ZDROJE = {"Automaticky": "auto", "Angličtina": "en", "Němčina": "de", "Francouzština": "fr",
                 "Španělština": "es", "Italština": "it", "Polština": "pl", "Slovenština": "sk",
                 "Ruština": "ru", "Ukrajinština": "uk"}

# Pravidla časování (viz tts.py, Hlas._delky).
ZRYCHLENI_BEZ_OBAV = 1.15   # do tohoto poměru stačí nativní duration OmniVoice
MIN_RYCHLOST = 0.85         # nikdy rychleji než 1/0,85 přirozené řeči
MAX_ZPOMALENI = 0.80        # krátkou větu zpomalit nejvýš na 0,8× přirozeného tempa
PRAH_KRATKE_VETY = 0.85     # zpomalovat, když hlas trvá méně než 85 % originálu
REZERVA_ZA_VETOU_S = 0.05   # tolik ticha nechat před další větou
MAX_POZICKA_S = 1.0         # kolik si věta smí půjčit z mezery za sebou

# Podklad = originál − g(t)·hlas mluvčího (viz maska.py). Kde g = 0, hraje originál
# beze změny; g je aktivita dabovaného mluvčího ze slov přepisu oříznutých detektorem řeči.
PRED_SLOVEM_S = 0.12        # odečítat kousek před slovem (časy Parakeetu mají přesnost ~80 ms)
ZA_SLOVEM_S = 0.15
NABEH_ODECTU_S = 0.04       # kosinová rampa před začátkem aktivity
DOZVUK_ODECTU_S = 0.25      # doběh za koncem aktivity (dozvuk sálu)
DOZVUK_STOP_DB = 3.0        # doběh utnout, když v hlasové stopě nastoupí nový zvuk (publikum)
SLOUCIT_MEZERU_S = 0.3      # mezery mezi úseky (po rezervách) kratší než tohle se vždy zaplní
VYPLNIT_MEZERU_S = 1.0      # pauzu téhož mluvčího do 1 s zaplnit, když v ní nenastoupí nový zvuk
                            # (jinak by podklad v rytmu řeči pumpoval)
VAD_PRAH = 0.35             # Silero: od jaké pravděpodobnosti je rámec řeč
VAD_REZERVA_S = 0.1         # Silero hlásí konec řeči o chlup dřív než doznění slova
SILERO_JIT = Path.home() / ".cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.jit"

# Hlasitost dabingu: věta se srovná s původním hlasem v tom místě. Cíl drží nejvýš ±4 dB
# od typické hlasitosti mluvčího (jásot ve větě by jinak češtinu vytáhl) a zisk věty
# nejvýš ±9 dB od typického zisku všech vět (chybné měření jedné věty nic nerozhodí).
MAX_ZISK_VETY_DB = 9.0
MAX_ODCHYLKA_MLUVCIHO_DB = 4.0
STROP_SPICKY_DBFS = -1.0    # špička hlasu + podkladu; podklad se nikdy neztišuje

SR_TTS = 24000
SR_MIX = 48000


def offline_env() -> dict[str, str]:
    """Prostředí pro podprocesy: modely jen z lokální cache, žádné stahování."""
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env
