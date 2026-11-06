"""Kontrola srozumitelnosti: nadabované věty zpátky přepsat Parakeetem (umí česky)
a porovnat s textem, který měl hlas říct (CER — podíl chybných znaků).

Vysoké CER prozradí zamumlanou, useknutou nebo vynechanou větu dřív, než ji
někdo uslyší. Cizí jména psaná foneticky („Mjúz") můžou CER mírně zvednout,
proto se hlásí až od prahu.

Vstup:  job.json (věty s `tts`)
Výstup: qa_zpet.json {id: {"cer": 0.07, "prepis": "…", "zkomolena": ["generaci→genesraci"]}}
(`zkomolena` = slova, která hlas vyslovil jako blízkou zkomoleninu; logika v dabing/qa_text.py)
"""

from __future__ import annotations

import json
import statistics
import sys

import soundfile as sf
from parakeet_mlx import from_pretrained

from dabing.config import MODEL_ASR
from dabing.qa_text import cer, oznac_zkomolena
from dabing.stages._spolecne import prubeh, slozka_z_argv


def main() -> None:
    slozka = slozka_z_argv()
    # `--jen 3,17,40`: přepsat jen tyhle věty a zbytek qa_zpet.json nechat (opakování po novém namluvení)
    jen = {int(x) for x in sys.argv[3].split(",") if x} if sys.argv[2:3] == ["--jen"] and len(sys.argv) > 3 else set()
    zakazka = json.loads((slozka / "job.json").read_text(encoding="utf-8"))
    vety = [v for v in zakazka["vety"] if v.get("tts")]
    cesta = slozka / "qa_zpet.json"
    vysledek = {}
    if jen:
        vety = [v for v in vety if v["id"] in jen]
        if cesta.exists():
            vysledek = json.loads(cesta.read_text(encoding="utf-8"))
    prubeh(0.0, "Načítám Parakeet pro kontrolu…")
    model = from_pretrained(MODEL_ASR)
    for i, v in enumerate(vety):
        info = sf.info(str(slozka / v["tts"]))
        # prázdný nebo skoro prázdný take (OmniVoice u jednoslovné věty) by Parakeet shodil
        # („as_strided: Negative dimensions") — věta bez hlasu je prostě nesrozumitelná
        prepis = model.transcribe(slozka / v["tts"]).text.strip() if info.frames >= info.samplerate * 0.1 else ""
        cil = v.get("vyslov") or v.get("cs") or ""
        vysledek[str(v["id"])] = {"cer": round(cer(cil, prepis), 3), "prepis": prepis}
        if i % 5 == 4:
            prubeh((i + 1) / len(vety), f"Kontroluji srozumitelnost {i + 1}/{len(vety)}…")
    texty = {str(v["id"]): v.get("vyslov") or v.get("cs") or "" for v in zakazka["vety"]}
    oznac_zkomolena(vysledek, texty)  # „generaci→genesraci": jedno zkomolené slovo CER sotva zvedne
    cesta.write_text(json.dumps(vysledek, ensure_ascii=False, indent=1), encoding="utf-8")
    median = statistics.median(x["cer"] for x in vysledek.values()) if vysledek else 0.0
    zkomolenych = sum(bool(x.get("zkomolena")) for x in vysledek.values())
    prubeh(1.0, f"Srozumitelnost: medián CER {median:.1%}, věty se zkomoleným slovem {zkomolenych}")


if __name__ == "__main__":
    main()
