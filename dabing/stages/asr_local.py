"""Krok 2: přepis s časy slov (Parakeet TDT 0.6B v3, MLX).

Vstup:  vocals16.wav (čistý hlas bez hudby — přesnější než celý mix)
Výstup: asr.json {"slova": [[slovo, od, do], …], "vety": [{"start", "end", "text", "jistota"}]}

Věty pro dabing skládá až segmenty.py ze souvislého seznamu slov: Parakeet
dělí dlouhé věty natvrdo po max_duration i uprostřed slova („its o" + „wn").

Chunkování má chybu (ověřeno na 16minutovém videu): uprostřed některého kousku
občas ztratí několik desítek sekund řeči úplně beze stopy — ne špatný přepis,
ale nic. Menší kousky riziko sníží (120 s → 204 s ztracené řeči, 60 s → 133 s,
30 s → 66 s na tomtéž videu), ale úplně ho nezaručí odstranit: jeden úsek zůstal
rozbitý i při 30 s. CHUNK_S = 45 s je kompromis mezi rizikem a rychlostí (víc
kousků = víc švů a delší výpočet). Jistotu dává až druhý krok: po hlavním
přepisu se najdou podezřele dlouhé mezery beze slov a přepíšou se ještě jednou
samostatně — stejný úsek přepsaný zvlášť projde bez problémů (`_doplnit_mezery`).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import soundfile as sf
from parakeet_mlx import DecodingConfig, SentenceConfig, from_pretrained

from dabing.config import MODEL_ASR
from dabing.stages._spolecne import prubeh, slozka_z_argv

CHUNK_S = 45.0
PREKRYV_S = 10.0
PRAH_MEZERY_S = 2.0     # mezera beze slov delší než tohle je podezřelá
PADDING_MEZERY_S = 2.0  # rezerva na obě strany, ať se slovo na okraji nepřepíše napůl


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


def _mezery(existujici: list[list], delka_s: float) -> list[tuple[float, float]]:
    """Úseky beze slov delší než PRAH_MEZERY_S, s rezervou na obě strany."""
    hranice = [(0.0, 0.0)] + [(w[1], w[2]) for w in existujici] + [(delka_s, delka_s)]
    return [(max(0.0, konec - PADDING_MEZERY_S), min(delka_s, zacatek + PADDING_MEZERY_S))
            for (_, konec), (zacatek, _) in zip(hranice, hranice[1:])
            if zacatek - konec > PRAH_MEZERY_S]


def _prekryva(a: float, b: float, existujici: list[list]) -> bool:
    return any(not (b <= w[1] + 0.05 or a >= w[2] - 0.05) for w in existujici)


def _doplnit_mezery(model, nastaveni: DecodingConfig, cesta_zvuku: Path,
                    vsechna: list[list], hlas_prubehu) -> list[list]:
    """Vrátí slova doplněná z podezřelých mezer (bez překryvu s tím, co už je)."""
    x, sr = sf.read(cesta_zvuku, dtype="float32")
    mezery = _mezery(vsechna, len(x) / sr)
    if not mezery:
        return []
    doplnena: list[list] = []
    with tempfile.TemporaryDirectory() as tmp:
        docasny = Path(tmp) / "mezera.wav"
        for i, (a, b) in enumerate(mezery):
            hlas_prubehu(i, len(mezery))
            usek = x[int(a * sr):int(b * sr)]
            if len(usek) < sr:  # moc krátké na smysluplný přepis
                continue
            sf.write(docasny, usek, sr)
            vysledek = model.transcribe(docasny, decoding_config=nastaveni,
                                        chunk_duration=CHUNK_S, overlap_duration=PREKRYV_S)
            for s in slova([t for v in vysledek.sentences for t in v.tokens]):
                zacatek, konec = s[1] + a, s[2] + a
                if not _prekryva(zacatek, konec, vsechna) and not _prekryva(zacatek, konec, doplnena):
                    doplnena.append([s[0], round(zacatek, 3), round(konec, 3)])
    return doplnena


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
        prubeh(0.05 + 0.8 * hotovo / max(celkem, 1), "Přepisuji řeč…")

    vysledek = model.transcribe(slozka / "vocals16.wav", decoding_config=nastaveni,
                                chunk_duration=CHUNK_S, overlap_duration=PREKRYV_S,
                                chunk_callback=hlaseni)
    vety = [{"start": round(v.start, 3), "end": round(v.end, 3), "text": v.text.strip(),
             "jistota": round(v.confidence, 3)} for v in vysledek.sentences if v.text.strip()]
    vsechna = slova([t for v in vysledek.sentences for t in v.tokens])

    def hlas_prubehu(i: int, celkem: int) -> None:
        prubeh(0.85 + 0.1 * i / max(celkem, 1), f"Kontroluji {celkem} podezřelých mezer v přepisu…")

    doplneno = _doplnit_mezery(model, nastaveni, slozka / "vocals16.wav", vsechna, hlas_prubehu)
    if doplneno:
        vsechna = sorted(vsechna + doplneno, key=lambda s: s[1])

    cil.write_text(json.dumps({"slova": vsechna, "vety": vety}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    zprava = f"Přepsáno {len(vsechna)} slov"
    if doplneno:
        zprava += f" ({len(doplneno)} doplněno z {round(sum(w[2] - w[1] for w in doplneno))} s mezer)"
    prubeh(1.0, zprava)


if __name__ == "__main__":
    main()
