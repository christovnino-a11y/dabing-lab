"""Krok 2: přepis s časy slov (Parakeet TDT 0.6B v3, MLX).

Vstup:  vocals16.wav (čistý hlas bez hudby — přesnější než celý mix)
Výstup: asr.json {"slova": [[slovo, od, do], …], "vety": [{"start", "end", "text", "jistota"}]}

Věty pro dabing skládá až segmenty.py ze souvislého seznamu slov: Parakeet
dělí dlouhé věty natvrdo po max_duration i uprostřed slova („its o" + „wn").
Dlouhé nahrávky jdou po 120s kouscích s 15s překryvem (vestavěné chunkování).
"""

from __future__ import annotations

import json

from parakeet_mlx import DecodingConfig, SentenceConfig, from_pretrained

from dabing.config import MODEL_ASR
from dabing.stages._spolecne import prubeh, slozka_z_argv


def slova(tokeny) -> list[list]:
    """Tokeny SentencePiece → slova. Nové slovo začíná tokenem s mezerou a písmenem
    nebo číslicí; samostatná interpunkce („ .", „ ,") se přilepí k předchozímu slovu."""
    vysledek: list[list] = []
    for t in tokeny:
        text = t.text
        nove = text.startswith(" ") and any(ch.isalnum() for ch in text)
        if not vysledek or nove:
            if text.strip():
                vysledek.append([text.strip(), float(t.start), float(t.end)])
        else:
            vysledek[-1][0] += text.strip() if text.startswith(" ") else text
            vysledek[-1][2] = float(t.end)
    return [s for s in vysledek if s[0]]


def main() -> None:
    slozka = slozka_z_argv()
    cil = slozka / "asr.json"
    if cil.exists():
        prubeh(1.0, "Přepis už existuje")
        return
    prubeh(0.0, "Načítám Parakeet…")
    model = from_pretrained(MODEL_ASR)
    nastaveni = DecodingConfig(sentence=SentenceConfig(max_duration=12.0, silence_gap=0.6))

    def hlaseni(hotovo: int, celkem: int) -> None:
        prubeh(0.05 + 0.9 * hotovo / max(celkem, 1), "Přepisuji řeč…")

    vysledek = model.transcribe(slozka / "vocals16.wav", decoding_config=nastaveni,
                                chunk_duration=120.0, overlap_duration=15.0,
                                chunk_callback=hlaseni)
    vety = [{"start": round(v.start, 3), "end": round(v.end, 3), "text": v.text.strip(),
             "jistota": round(v.confidence, 3)} for v in vysledek.sentences if v.text.strip()]
    vsechna = slova([t for v in vysledek.sentences for t in v.tokens])
    cil.write_text(json.dumps({"slova": vsechna, "vety": vety}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    prubeh(1.0, f"Přepsáno {len(vsechna)} slov")


if __name__ == "__main__":
    main()
