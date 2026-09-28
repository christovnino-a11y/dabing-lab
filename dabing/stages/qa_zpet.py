"""Kontrola srozumitelnosti: nadabované věty zpátky přepsat Parakeetem (umí česky)
a porovnat s textem, který měl hlas říct (CER — podíl chybných znaků).

Vysoké CER prozradí zamumlanou, useknutou nebo vynechanou větu dřív, než ji
Nino uslyší. Cizí jména psaná foneticky („Mjúz") můžou CER mírně zvednout,
proto se hlásí až od prahu.

Vstup:  job.json (věty s `tts`)
Výstup: qa_zpet.json {id: {"cer": 0.07, "prepis": "…"}}
"""

from __future__ import annotations

import json
import re
import unicodedata

from parakeet_mlx import from_pretrained

from dabing.config import MODEL_ASR
from dabing.stages._spolecne import prubeh, slozka_z_argv


def _normalizuj(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cer(ref: str, hyp: str) -> float:
    a, b = _normalizuj(ref), _normalizuj(hyp)
    if not a:
        return 0.0 if not b else 1.0
    predchozi = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        aktualni = [i]
        for j, cb in enumerate(b, 1):
            aktualni.append(min(predchozi[j] + 1, aktualni[j - 1] + 1,
                                predchozi[j - 1] + (ca != cb)))
        predchozi = aktualni
    return predchozi[-1] / len(a)


def main() -> None:
    slozka = slozka_z_argv()
    zakazka = json.loads((slozka / "job.json").read_text(encoding="utf-8"))
    vety = [v for v in zakazka["vety"] if v.get("tts")]
    prubeh(0.0, "Načítám Parakeet pro kontrolu…")
    model = from_pretrained(MODEL_ASR)
    vysledek = {}
    for i, v in enumerate(vety):
        prepis = model.transcribe(slozka / v["tts"]).text.strip()
        cil = v.get("vyslov") or v.get("cs") or ""
        vysledek[str(v["id"])] = {"cer": round(cer(cil, prepis), 3), "prepis": prepis}
        if i % 5 == 4:
            prubeh((i + 1) / len(vety), f"Kontroluji srozumitelnost {i + 1}/{len(vety)}…")
    (slozka / "qa_zpet.json").write_text(json.dumps(vysledek, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    hodnoty = sorted(x["cer"] for x in vysledek.values())
    median = hodnoty[len(hodnoty) // 2] if hodnoty else 0.0
    prubeh(1.0, f"Srozumitelnost: medián CER {median:.1%}")


if __name__ == "__main__":
    main()
