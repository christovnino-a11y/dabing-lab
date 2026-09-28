"""Krok 2: přepis s časy slov (Parakeet TDT 0.6B v3, MLX).

Vstup:  vocals16.wav (čistý hlas bez hudby — přesnější než celý mix)
Výstup: asr.json {"slova": [[slovo, od, do], …], "vety": [{"start", "end", "text", "jistota"}]}

Věty pro dabing skládá až segmenty.py ze souvislého seznamu slov: Parakeet
dělí dlouhé věty natvrdo po max_duration i uprostřed slova („its o" + „wn").

Proč vlastní dělení na úseky (ověřeno na 16minutovém videu, srovnáno s Whisperem):
- Vestavěné chunkování Parakeetu (kousky s překryvem, které pak slepí) uprostřed
  dlouhého kousku občas ztratí desítky sekund řeči úplně beze stopy (120s kousky:
  321 slov v 11 dírách) a na švech překryvů nechává zdvojená, rozsekaná nebo
  časově obrácená slova („doing.. doing.", „W butell").
- Proto se zvuk dělí sám v nejtišších místech na úseky 25–45 s a každý se přepíše
  samostatně, bez překryvu — žádné švy k lepení, krátké úseky Parakeet zvládá.
- Pojistka: mezery beze slov delší než 2 s se přepíšou ještě jednou zvlášť
  (`_doplnit_mezery`); bere se jen slovo s kladnou délkou a slyšitelným zvukem.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from parakeet_mlx import DecodingConfig, SentenceConfig, from_pretrained

from dabing.config import MODEL_ASR
from dabing.stages._spolecne import prubeh, slozka_z_argv

USEK_MIN_S = 25.0       # vlastní úseky: řez v nejtišším místě mezi 25 a 45 s
USEK_MAX_S = 45.0
RAMEC_S = 0.05          # rámec energie pro hledání ticha
PRAH_MEZERY_S = 2.0     # mezera beze slov delší než tohle je podezřelá
PADDING_MEZERY_S = 3.0  # rezerva na obě strany (krátký úsek Parakeet občas vrátí prázdný)
MIN_HLASITOST_DBFS = -55.0  # doplněné slovo musí mít slyšitelný zvuk (proti halucinacím v tichu)


def slova(tokeny) -> list[list]:
    """Tokeny SentencePiece → slova [text, od, do].

    Nové slovo začíná tokenem s mezerou a písmenem/číslicí, nebo tokenem po samostatné
    mezeře (Parakeet posílá čísla jako ' ', '2', '4', '9' — bez toho by se „249" přilepilo
    k předchozímu slovu: „just249."). Interpunkce se přilepí k předchozímu slovu, ale
    neprodlouží jeho konec (tečka jinak dostane čas konce následující pauzy)."""
    vysledek: list[list] = []
    po_mezere = False
    for t in tokeny:
        text = t.text
        if not text.strip():
            po_mezere = True
            continue
        zacina = text.startswith(" ") or po_mezere
        po_mezere = False
        obsah = any(ch.isalnum() for ch in text)
        if not vysledek or (zacina and (obsah or text.strip()[0] in "$€£")):
            vysledek.append([text.strip(), float(t.start), float(t.end)])
        else:
            vysledek[-1][0] += text.strip()
            if obsah:
                vysledek[-1][2] = float(t.end)
    return [s for s in vysledek if s[0]]


def _vycisti(seznam: list[list]) -> list[list]:
    """Seřadí slova a vyhodí rozbitá: nulová nebo záporná délka, překryv s předchozím
    (artefakt slepení — skutečné zadrhnutí „the the" má časy za sebou, ne přes sebe)."""
    vysledek: list[list] = []
    for w in sorted(seznam, key=lambda w: (w[1], w[2])):
        if w[2] - w[1] < 0.02:
            continue
        if vysledek and w[1] < vysledek[-1][2] - 0.02:
            continue
        vysledek.append(w)
    return vysledek


def _hranice_useku(x: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """Rozdělí nahrávku na úseky 25–45 s, řez vždy v nejtišším 50ms rámci."""
    r = int(RAMEC_S * sr)
    n = len(x) // r
    energie = (x[:n * r].reshape(n, r) ** 2).mean(axis=1) if n else np.zeros(0)
    delka = len(x) / sr
    useky, zacatek = [], 0.0
    while delka - zacatek > USEK_MAX_S:
        i0, i1 = int((zacatek + USEK_MIN_S) / RAMEC_S), int((zacatek + USEK_MAX_S) / RAMEC_S)
        rez = (i0 + int(np.argmin(energie[i0:i1])) + 0.5) * RAMEC_S
        useky.append((zacatek, rez))
        zacatek = rez
    useky.append((zacatek, delka))
    return useky


def _prepis_useku(model, nastaveni, x: np.ndarray, sr: int, a: float, b: float,
                  docasny: Path) -> tuple[list[list], list]:
    """Samostatný přepis úseku [a, b] s posunem časů; vrací slova a věty."""
    sf.write(docasny, x[int(a * sr):int(b * sr)], sr)
    vysledek = model.transcribe(docasny, decoding_config=nastaveni)
    posun = [[w[0], round(w[1] + a, 3), round(w[2] + a, 3)]
             for w in slova([t for v in vysledek.sentences for t in v.tokens])]
    return posun, [(v, a) for v in vysledek.sentences]


def _hlasitost_dbfs(x: np.ndarray, sr: int, a: float, b: float) -> float:
    usek = x[int(a * sr):max(int(b * sr), int(a * sr) + 1)]
    return 20 * float(np.log10(np.sqrt(np.mean(usek ** 2)) + 1e-9))


def _mezery(existujici: list[list], delka_s: float) -> list[tuple[float, float]]:
    """Úseky beze slov delší než PRAH_MEZERY_S, s rezervou na obě strany."""
    hranice = [(0.0, 0.0)] + [(w[1], w[2]) for w in existujici] + [(delka_s, delka_s)]
    return [(max(0.0, konec - PADDING_MEZERY_S), min(delka_s, zacatek + PADDING_MEZERY_S))
            for (_, konec), (zacatek, _) in zip(hranice, hranice[1:])
            if zacatek - konec > PRAH_MEZERY_S]


def _prekryva(a: float, b: float, existujici: list[list]) -> bool:
    return any(w[1] < b and a < w[2] for w in existujici)


def _doplnit_mezery(model, nastaveni, x: np.ndarray, sr: int, vsechna: list[list],
                    docasny: Path, hlas_prubehu) -> list[list]:
    """Slova doplněná z podezřelých mezer — jen kladná délka, slyšitelný zvuk, bez překryvu."""
    mezery = _mezery(vsechna, len(x) / sr)
    doplnena: list[list] = []
    for i, (a, b) in enumerate(mezery):
        hlas_prubehu(i, len(mezery))
        if b - a < 1.0:
            continue
        nova, _ = _prepis_useku(model, nastaveni, x, sr, a, b, docasny)
        for w in nova:
            if (w[2] - w[1] >= 0.05 and _hlasitost_dbfs(x, sr, w[1], w[2]) > MIN_HLASITOST_DBFS
                    and not _prekryva(w[1], w[2], vsechna) and not _prekryva(w[1], w[2], doplnena)):
                doplnena.append(w)
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
    x, sr = sf.read(slozka / "vocals16.wav", dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)

    vsechna: list[list] = []
    vety_raw: list = []
    with tempfile.TemporaryDirectory() as tmp:
        docasny = Path(tmp) / "usek.wav"
        useky = _hranice_useku(x, sr)
        for i, (a, b) in enumerate(useky):
            prubeh(0.05 + 0.8 * i / len(useky), "Přepisuji řeč…")
            nova, vety = _prepis_useku(model, nastaveni, x, sr, a, b, docasny)
            vsechna += nova
            vety_raw += vety
        vsechna = _vycisti(vsechna)

        def hlas_prubehu(i: int, celkem: int) -> None:
            prubeh(0.85 + 0.1 * i / max(celkem, 1), f"Kontroluji {celkem} podezřelých mezer v přepisu…")

        doplneno = _doplnit_mezery(model, nastaveni, x, sr, vsechna, docasny, hlas_prubehu)
    if doplneno:
        vsechna = _vycisti(vsechna + doplneno)

    vety = [{"start": round(v.start + a, 3), "end": round(v.end + a, 3), "text": v.text.strip(),
             "jistota": round(v.confidence, 3)} for v, a in vety_raw if v.text.strip()]
    cil.write_text(json.dumps({"slova": vsechna, "vety": vety}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    zprava = f"Přepsáno {len(vsechna)} slov ({len(useky)} úseků)"
    if doplneno:
        zprava += f", {len(doplneno)} doplněno z mezer"
    prubeh(1.0, zprava)


if __name__ == "__main__":
    main()
