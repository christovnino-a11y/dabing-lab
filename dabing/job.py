"""Zakázka dabingu: složka `jobs/<id>/` a v ní `job.json`.

Jedna zakázka = jedno video. ID je YouTube ID (nový odkaz = nová zakázka),
u nahraného souboru otisk obsahu. Kroky pipeline si předávají data přes soubory
ve složce, takže každý těžký krok může běžet jako samostatný proces.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import JOBS


@dataclass
class Veta:
    id: int
    start: float
    end: float
    mluvci: int = 0
    en: str = ""                    # text z přepisu (přesně k času; i text reference hlasu)
    en_titulky: str = ""            # text z titulků YouTube, kde se liší od přepisu (jde do překladu)
    slova: list = field(default_factory=list)  # [[slovo, od, do], …] z přepisu
    cista: bool = True               # v blízkosti nemluví nikdo jiný (vhodná reference)
    cs: str = ""                    # překlad pro titulky a tabulku
    vyslov: str = ""                # totéž pro hlas: cizí jména a zkratky foneticky
    upraveno: bool = False          # CS text ručně změněný v UI
    zkraceno: bool = False          # Codex větu zkracoval, aby se vešla
    max_znaku: int = 0
    cil_znaku: int = 0              # kolik znaků zhruba vyplní čas originálu
    reference: str = ""             # cesta k referenci hlasu (relativně ke složce)
    druh_reference: str = ""        # "kotva" (česká věta mluvčího), "veta" (emoce z originálu), "mluvci"
    tts: str = ""                   # vygenerovaný hlas (relativně ke složce)
    tts_otisk: str = ""
    prirozena_s: float = 0.0        # délka bez vynucené délky
    vysledna_s: float = 0.0
    vynucena_s: float | None = None # duration předaná OmniVoice
    pomer: float = 0.0              # přirozená délka / slot
    identita: float | None = None   # podobnost ECAPA s hlavní referencí mluvčího
    seed: int = 0                   # náhoda generování (hlídač identity ho při vybočení zvýší)
    qa: list[str] = field(default_factory=list)

    @property
    def delka(self) -> float:
        return self.end - self.start


@dataclass
class Zakazka:
    id: str
    zdroj: str
    nazev: str = ""
    popis: str = ""                 # popis videa z YouTube (napoví překladači jména)
    delka_s: float = 0.0
    jazyk: str = "cs"
    jazyk_zdroje: str = "auto"      # jazyk originálu ("auto" = pozná překladač sám)
    prekladac: str = "codex"        # "codex" nebo "offline"
    pocet_mluvcich: int | None = None
    vytvoreno: str = ""
    kroky: dict[str, str] = field(default_factory=dict)  # krok -> čas dokončení
    codex: dict = field(default_factory=dict)            # tokeny a kvóta
    rychlost_hlasu: dict = field(default_factory=dict)   # mluvčí → znaků/s změřeno z kotvy
    vety: list[Veta] = field(default_factory=list)

    # ------------------------------------------------------------ soubory
    @property
    def slozka(self) -> Path:
        return JOBS / self.id

    def cesta(self, jmeno: str) -> Path:
        return self.slozka / jmeno

    def uloz(self) -> None:
        self.slozka.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        docasny = self.cesta("job.json.tmp")
        docasny.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(docasny, self.cesta("job.json"))

    @classmethod
    def nacti(cls, id_nebo_slozka: str | Path) -> "Zakazka":
        slozka = Path(id_nebo_slozka)
        if not slozka.is_absolute():
            slozka = JOBS / str(id_nebo_slozka)
        data = json.loads((slozka / "job.json").read_text(encoding="utf-8"))
        platne = {f.name for f in fields(Veta)}
        vety = [Veta(**{k: v for k, v in x.items() if k in platne}) for x in data.pop("vety", [])]
        platne_z = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in platne_z}, vety=vety)

    def hotovo(self, krok: str) -> None:
        self.kroky[krok] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.uloz()


def id_ze_souboru(cesta: Path) -> str:
    """Otisk nahraného souboru: velikost + prvních 8 MB (rychlé i u velkých videí)."""
    h = hashlib.sha1()
    h.update(str(cesta.stat().st_size).encode())
    with open(cesta, "rb") as f:
        h.update(f.read(8 * 1024 * 1024))
    return "soubor-" + h.hexdigest()[:12]


def bezpecne_jmeno(text: str, max_delka: int = 80) -> str:
    jmeno = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    jmeno = re.sub(r"\s+", " ", jmeno)
    return (jmeno or "dabing")[:max_delka]


def otisk_tts(veta: Veta, jazyk: str, kroky: int, reference_mtime: float) -> str:
    """Co když se změní, věta se musí vygenerovat znovu."""
    klic = json.dumps([veta.vyslov or veta.cs, jazyk, kroky, veta.reference, round(reference_mtime, 3),
                       veta.vynucena_s, veta.seed], ensure_ascii=False)
    return hashlib.sha1(klic.encode()).hexdigest()[:16]
