"""Porovnání textu, který měl hlas říct, s jeho zpětným přepisem — čisté funkce bez modelů.

Žije mimo `stages/qa_zpet.py` (ten importuje Parakeet), aby šla logika importovat i z pipeline,
z UI a z testů. Dvě míry:
- `cer`: podíl chybných znaků celé věty (zachytí zamumlanou, useknutou nebo vynechanou větu),
- `zkomolena_slova`: jednotlivá slova, která hlas vyslovil jako zkomolenou variantu („generaci" →
  „genesraci"). V dlouhé větě takové slovo CER sotva zvedne (~0,10), a přesto ho Nino uslyší.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

# Zkomolené slovo: aspoň tolik písmen (kratší slova ASR i TTS mění běžně a nepoznatelně).
MIN_DELKA_SLOVA = 5
# Podobnost znaků referenčního a přepsaného slova: pod dolní mezí je to jiné slovo (ASR slyšel
# něco jiného nebo hlas řekl úplně jinou věc), od horní mezí je to prakticky totéž slovo.
PODOBNOST_OD = 0.5
PODOBNOST_DO = 0.95
# Přepsané slovo musí mít aspoň tenhle podíl délky referenčního (kratší zbytek je jiné slovo/kus).
MIN_PODIL_DELKY = 0.6
# Konec věty před slovem: velké písmeno za ním je jen začátek věty, ne vlastní jméno.
_KONEC_VETY = ".!?…"
_UVOZOVKY = "\"'„“”‚‘’»«()[]–—- "


def normalizuj(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cer(ref: str, hyp: str) -> float:
    a, b = normalizuj(ref), normalizuj(hyp)
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


def _bez_diakritiky(slovo: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", slovo) if not unicodedata.combining(c))


# Cizí slova ASR píše po svém (hlas řekl správně „klaud", Parakeet napsal „cloud"): takový rozdíl
# není zkomolení. Zvuková podoba slova = bez diakritiky, w→v, y→i, ph→f, ck→k, c→k/s, zdvojené
# souhlásky jednoduše; shodná zvuková podoba nebo „anglický" pravopis přepsaného slova = pravopis.
_ANGLICKY_PRAVOPIS = re.compile(r"[wqx]|ck|th|ph|oo|ee|ea|gh|sh|ay|ey|ow|^cl|ing$|ign")


def _zvuk(slovo: str) -> str:
    s = _bez_diakritiky(slovo)
    s = s.replace("ph", "f").replace("ck", "k").replace("w", "v").replace("y", "i").replace("q", "k")
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
    return re.sub(r"(.)\1", r"\1", s)


def _slova(text: str) -> list[tuple[str, bool, bool, bool]]:
    """[(slovo v původní velikosti písmen, smí být zkomolené, je jméno, je na začátku věty)] v pořadí jako
    `normalizuj(text).split()`. Jméno = velké písmeno mimo začátek věty nebo velké písmeno uvnitř
    slova (GPU, OmniVoice); smí být zkomolené = ne jméno, ne číslo, dost dlouhé."""
    text = unicodedata.normalize("NFC", text)
    vysledek = []
    for shoda in re.finditer(r"\w+", text):
        slovo = shoda.group()
        pred = text[:shoda.start()].rstrip(_UVOZOVKY)
        zacatek_vety = not pred or pred[-1] in _KONEC_VETY
        jmeno = (slovo[0].isupper() and not zacatek_vety) or any(c.isupper() for c in slovo[1:])
        cislo = any(c.isdigit() for c in slovo) or "_" in slovo
        vysledek.append((slovo, not (jmeno or cislo) and len(slovo) >= MIN_DELKA_SLOVA, jmeno, zacatek_vety))
    return vysledek


def slovnik_jmen(texty: Iterable[str]) -> frozenset[str]:
    """Začátky (5 písmen) vlastních jmen, která se v textech objevila velkým písmenem uprostřed
    věty (Nvidia, Evropa). Jméno na začátku věty se jinak nepozná od obyčejného slova, a foneticky
    psané („Envidia") by se pak hlásilo jako zkomolené, ač ho hlas řekl dobře."""
    return frozenset(s.lower()[:5] for t in texty for s, _, jmeno, _ in _slova(t)
                     if jmeno and len(s) >= 4 and s.isalpha() and not s.isupper())


def _zkomolene_ve_dvojici(ref: str, hyp: str) -> bool:
    """Je `hyp` zkomolená podoba slova `ref`, ne jiné slovo, jiný tvar ani jiný pravopis?"""
    if any(c.isdigit() for c in hyp) or len(hyp) < MIN_PODIL_DELKY * len(ref):
        return False  # ASR napsal číslicí / z věty zbyl jen kousek slova
    if _zvuk(ref) == _zvuk(hyp) or _ANGLICKY_PRAVOPIS.search(hyp):
        return False
    spolecny = 0
    for a, b in zip(ref, hyp):
        if a != b:
            break
        spolecny += 1
    if spolecny >= min(len(ref), len(hyp)) - 2:
        return False  # liší se jen koncovka (generace/generaci): jiný tvar, ne zkomolení
    return PODOBNOST_OD <= difflib.SequenceMatcher(None, ref, hyp).ratio() < PODOBNOST_DO


def zkomolena_slova(ref: str, hyp: str, jmena: frozenset[str] = frozenset()) -> list[str]:
    """Slova z `ref` (text, který měl hlas říct, s původní velikostí písmen), která se v přepisu
    `hyp` objevila jako blízká zkomolenina: ["generaci→genesraci", …]. Párují se jen slova
    v blocích „nahrazeno" (difflib nad seznamy slov); rozdělení a slepení slov ASR se nepočítá.
    Jméno, které Parakeet v přepisu napsal velkým písmenem (Nvidia, Paskal) nebo které je ve
    slovníku `jmena` (`slovnik_jmen`), se nepočítá."""
    slova = _slova(ref)
    a = [s[0].lower() for s in slova]
    slova_hyp = _slova(hyp)
    b = [s[0].lower() for s in slova_hyp]
    if not a or not b:
        return []
    nalezena = []
    for operace, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if operace != "replace":
            continue
        if "".join(a[i1:i2]) == "".join(b[j1:j2]):
            continue  # slepené/rozdělené slovo ASR
        navrhy = sorted(((difflib.SequenceMatcher(None, a[i], b[j]).ratio(), i, j)
                         for i in range(i1, i2) if slova[i][1] for j in range(j1, j2)), reverse=True)
        pouzite_i, pouzite_j = set(), set()
        for _, i, j in navrhy:
            if i in pouzite_i or j in pouzite_j:
                continue
            pouzite_i.add(i)
            pouzite_j.add(j)
            if slova_hyp[j][2]:
                continue  # Parakeet to napsal jako jméno
            if (slova[i][3] or slova_hyp[j][3]) and (a[i][:5] in jmena or b[j][:5] in jmena):
                continue  # jméno na začátku věty, které je jinde ve videu uprostřed věty
            if _zkomolene_ve_dvojici(a[i], b[j]):
                nalezena.append((i, f"{a[i]}→{b[j]}"))
    return [x for _, x in sorted(nalezena)]


def oznac_zkomolena(zaznamy: dict[str, dict], texty: dict[str, str], prepsat: bool = True) -> bool:
    """Do záznamů `qa_zpet.json` ({id: {"cer", "prepis"}}) doplní `zkomolena` — seznam
    „slovo→přepis" — podle textu věty `texty[id]` a jmen z celého videa. S `prepsat=False` jen
    tam, kde pole chybí (starší soubory). Vrací, zda se něco změnilo."""
    jmena = slovnik_jmen([z["prepis"] for z in zaznamy.values()] + list(texty.values()))
    zmeneno = False
    for id_, zaznam in zaznamy.items():
        if not prepsat and "zkomolena" in zaznam:
            continue
        nova = zkomolena_slova(texty.get(id_, ""), zaznam["prepis"], jmena)
        if zaznam.get("zkomolena") != nova:
            zaznam["zkomolena"] = nova
            zmeneno = True
    return zmeneno


def nacti_qa(cesta: Path, vety: Iterable, zapsat: bool = False) -> dict[str, dict]:
    """Načte `qa_zpet.json` ({} když chybí) a dopočítá `zkomolena` u záznamů, které ho nemají
    (soubory vzniklé před touto kontrolou); text věty je `vyslov` nebo `cs`. S `zapsat` doplněné
    uloží zpět."""
    if not cesta.exists():
        return {}
    data = json.loads(cesta.read_text(encoding="utf-8"))
    texty = {str(v.id): v.vyslov or v.cs for v in vety}
    if oznac_zkomolena(data, texty, prepsat=False) and zapsat:
        cesta.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data
