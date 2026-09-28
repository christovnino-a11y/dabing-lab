"""Ruční titulky od autora videa jako text vět pro překlad (stahuje je media.stahni_titulky).

Nino 2026-09-28: „nejdřív originál, když nepůjde, naše STT". Text věty pro překladač se bere
z titulků od autora, kde je mají; jinak z našeho přepisu. Překladač u rozdílných vět vidí obojí.
Automatické titulky YouTube se nepoužívají (viz media._vyber_titulky): Codex s nimi převzal
jejich věrohodné chyby, kdežto nesmysly našeho přepisu opraví podle smyslu sám.
Časy vět, maska odečtu i text reference hlasu (Veta.en) zůstávají z přepisu: titulky mají jen
přibližné časy.

Slovo titulků patří té větě, do které patří odpovídající slovo přepisu (zarovnání přes difflib),
takže se na hranicích vět nic neztratí ani nezdvojí; slova bez protějšku se zařadí podle času.
"""

from __future__ import annotations

import difflib
import json
import re
from bisect import bisect_right

from .job import Zakazka

OKRAJ_S = 0.3  # slovo navíc v titulcích patří větě, do jejíhož času (± okraj) spadá
MIN_SHODA = 0.5  # aspoň tolik slov titulků se musí doslova shodovat s přepisem (Meta Connect: 0,96)


def normuj(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def _nacti(zakazka: Zakazka) -> dict | None:
    cesta = zakazka.cesta("titulky.json")
    if not cesta.exists():
        return None
    try:
        data = json.loads(cesta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("druh") == "rucni" and isinstance(data.get("slova"), list) else None


def druh(zakazka: Zakazka) -> str:
    return "ruční od autora" if _nacti(zakazka) else ""


def prirad(zakazka: Zakazka) -> int:
    """Doplní Veta.en_titulky tam, kde se titulky liší od přepisu. Vrací počet takových vět."""
    for v in zakazka.vety:
        v.en_titulky = ""
    data = _nacti(zakazka)
    if data is None or not zakazka.vety:
        return 0
    titulky = data["slova"]
    vety = sorted(zakazka.vety, key=lambda v: v.start)
    prepis = [(k, w[0]) for k, v in enumerate(vety) for w in v.slova]
    a = [normuj(w).replace(" ", "") for _, w in prepis]
    b = [normuj(w).replace(" ", "") for w, _ in titulky]
    kam: list[int | None] = [None] * len(titulky)
    shodnych = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        shodnych += (j2 - j1) if tag == "equal" else 0
        if tag in ("equal", "replace"):
            for j in range(j1, j2):
                kam[j] = prepis[i1 + (j - j1) * (i2 - i1) // (j2 - j1)][0]
    if shodnych < MIN_SHODA * max(len(b), 1):
        return 0  # titulky nesedí k řeči (jiný jazyk nebo jiná verze videa) — nepoužít
    starty = [v.start for v in vety]
    for j, (_, t) in enumerate(titulky):
        if kam[j] is None:
            k = bisect_right(starty, t + OKRAJ_S) - 1
            if k >= 0 and t <= vety[k].end + OKRAJ_S:
                kam[j] = k
    texty: dict[int, list[str]] = {}
    for j, k in enumerate(kam):
        if k is not None:
            texty.setdefault(k, []).append(titulky[j][0])
    lisi = 0
    for k, v in enumerate(vety):
        text = " ".join(texty.get(k, []))
        if text and normuj(text) != normuj(v.en):
            v.en_titulky = text
            lisi += 1
    return lisi
