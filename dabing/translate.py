"""Překlad vět pro dabing s rozpočtem délky: Codex (výchozí) nebo offline LM Studio.

Codex dostává věty po dávkách (každý turn nese ~18k tokenů režie, takže po
jedné větě by to bylo drahé a pomalé). Každá věta má limit znaků odvozený z délky
původní řeči; co se nevejde, jde do dávkového zkrácení.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import requests

from .codex_client import Codex, text
from .job import Veta, Zakazka

Log = Callable[[str], None]

NAZVY_JAZYKU = {"cs": "češtiny", "sk": "slovenštiny", "de": "němčiny", "en": "angličtiny"}
ZE_JAZYKA = {"en": "z angličtiny", "de": "z němčiny", "fr": "z francouzštiny", "es": "ze španělštiny",
             "it": "z italštiny", "pl": "z polštiny", "sk": "ze slovenštiny", "ru": "z ruštiny",
             "uk": "z ukrajinštiny", "auto": "z jazyka originálu (rozpoznej ho sám)"}
DAVKA_ZNAKU_CODEX = 6000
DAVKA_ZNAKU_OFFLINE = 2500
TOLERANCE = 1.05
LM_STUDIO = "http://127.0.0.1:1234/v1"

SCHEMA_PREKLAD = {
    "type": "object",
    "properties": {
        "vety": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "cs": {"type": "string"},
                               "vyslov": {"type": "string"}},
                "required": ["id", "cs", "vyslov"],
                "additionalProperties": False,
            },
        },
        "glosar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"en": {"type": "string"}, "cs": {"type": "string"}},
                "required": ["en", "cs"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["vety", "glosar"],
    "additionalProperties": False,
}


def pokyny(jazyk: str, zdroj: str = "auto") -> str:
    cilovy = NAZVY_JAZYKU.get(jazyk, jazyk)
    odkud = ZE_JAZYKA.get(zdroj, ZE_JAZYKA["auto"])
    return f"""Jsi zkušený dabingový překladatel {odkud} do {cilovy}.
Dostaneš očíslované věty z videa (mluvčí, text originálu v poli `en` a limity znaků). Pravidla:
- Každou větu přelož do přirozené mluvené řeči, jak by ji řekl dabér. Věrně významem, ne doslovně.
- Délka: každá věta má `cil_znaku` (tolik znaků vyplní čas, kdy mluví originál) a `limit_znaku`
  (strop). Přibliž se cíli — překládej úplně a věrně, nic zbytečně nevynechávej, ať po větě
  nezůstane ticho, zatímco řečník na obraze ještě mluví. Limit nikdy nepřekračuj; když se
  obsah nevejde, zkrať vatu a opakování.
- Jména, názvy produktů a firem nech tak, jak se běžně říkají. Stejný termín překládej vždy stejně
  (drž se glosáře, pokud ho dostaneš, a vrať ho doplněný).
- Čísla a jednotky piš slovy.
- Ke každé větě vrať i pole `vyslov`: stejný překlad, ale cizí jména, názvy a zkratky přepsané
  foneticky česky tak, jak je má dabér vyslovit (např. „Muse" → „Mjúz", „VM" → „ví em",
  „AI" → „éj áj", „Meta" → „Meta"). Český hlasový model čte text česky, proto to potřebuje.
  V `cs` nech jména normálně (to jsou titulky).
- Oslovení (tykání/vykání) drž konzistentní podle kontextu.
- Nic nepřidávej, nekomentuj a nepoužívej žádné nástroje ani soubory.
Vrať JSON podle schématu: pro každé id pole `cs` a `vyslov` a glosář použitých termínů."""


def _davky(vety: list[Veta], max_znaku: int) -> list[list[Veta]]:
    davky, aktualni, znaky = [], [], 0
    for v in vety:
        if aktualni and znaky + len(v.en) > max_znaku:
            davky.append(aktualni)
            aktualni, znaky = [], 0
        aktualni.append(v)
        znaky += len(v.en) + 40
    if aktualni:
        davky.append(aktualni)
    return davky


def _zadani(davka: list[Veta], kontext: list[Veta], glosar: list[dict], zkratit: bool) -> str:
    data = {
        "kontext_predchozich_vet": [{"en": v.en, "cs": v.cs} for v in kontext],
        "glosar": glosar,
        "vety": [{"id": v.id, "mluvci": f"M{v.mluvci + 1}", "en": v.en,
                  **({"soucasny_preklad": v.cs, "soucasna_vyslovnost": v.vyslov or v.cs,
                     "limit_znaku": v.max_znaku} if zkratit
                     else {"cil_znaku": v.cil_znaku, "limit_znaku": v.max_znaku})}
                 for v in davka],
    }
    uvod = ("ZKRAŤ tyto překlady tak, aby se vešly do limitu znaků. Zachovej hlavní význam.\n"
            if zkratit else "Přelož tyto věty.\n")
    return uvod + json.dumps(data, ensure_ascii=False)


class _Codex:
    def __init__(self, zakazka: Zakazka, log: Log):
        self.zakazka, self.log = zakazka, log
        self.codex = Codex()
        self.turny, self.vstup, self.vystup = 0, 0, 0

    def __enter__(self):
        self.codex.start()
        limity = self.codex.limity().get("rateLimits", {})
        self.zakazka.codex["kvota_pred_%"] = (limity.get("primary") or {}).get("usedPercent")
        return self

    def __exit__(self, *_):
        try:
            limity = self.codex.limity().get("rateLimits", {})
            self.zakazka.codex["kvota_po_%"] = (limity.get("primary") or {}).get("usedPercent")
        finally:
            self.codex.close()
        self.zakazka.codex.update(turny=self.zakazka.codex.get("turny", 0) + self.turny,
                                  vstupni_tokeny=self.zakazka.codex.get("vstupni_tokeny", 0) + self.vstup,
                                  vystupni_tokeny=self.zakazka.codex.get("vystupni_tokeny", 0) + self.vystup)

    def dotaz(self, zadani: str) -> dict:
        r = self.codex.turn([text(zadani)], schema=SCHEMA_PREKLAD, pokyny=pokyny(self.zakazka.jazyk, self.zakazka.jazyk_zdroje))
        self.turny += 1
        posledni = (r.tokeny or {}).get("total") or {}
        self.vstup += int(posledni.get("inputTokens", 0))
        self.vystup += int(posledni.get("outputTokens", 0))
        if r.status != "completed" or not r.text:
            raise RuntimeError(f"Codex překlad selhal ({r.status}): {r.chyba or 'prázdná odpověď'}")
        if "commandExecution" in r.typy_polozek:
            self.log("Pozor: Codex si během překladu spouštěl příkazy.")
        return r.json()


class _Offline:
    """LM Studio na :1234 (OpenAI-kompatibilní API), model, který je v něm načtený."""

    def __init__(self, zakazka: Zakazka, log: Log):
        self.zakazka, self.log = zakazka, log
        self.model = ""

    def __enter__(self):
        try:
            modely = requests.get(f"{LM_STUDIO}/models", timeout=5).json().get("data", [])
        except requests.RequestException as chyba:
            raise RuntimeError("LM Studio neběží. Spusť v něm server (Developer → Start Server) "
                               "a načti model.") from chyba
        jazykove = [m["id"] for m in modely if "embed" not in m["id"].lower()]
        if not jazykove:
            raise RuntimeError("V LM Studiu není načtený žádný jazykový model.")
        self.model = jazykove[0]
        self.log(f"Offline překlad přes LM Studio: {self.model}")
        return self

    def __exit__(self, *_):
        return False

    def dotaz(self, zadani: str) -> dict:
        odpoved = requests.post(f"{LM_STUDIO}/chat/completions", timeout=900, json={
            "model": self.model,
            "temperature": 0.3,
            "max_tokens": 12000,
            "messages": [{"role": "system", "content": pokyny(self.zakazka.jazyk, self.zakazka.jazyk_zdroje)},
                         {"role": "user", "content": zadani}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "preklad", "strict": True,
                                                "schema": SCHEMA_PREKLAD}},
        }).json()
        obsah = ((odpoved.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not obsah.strip():
            raise RuntimeError("Model v LM Studiu vrátil prázdnou odpověď. U Gemmy 4 vypni v LM Studiu "
                               "přemýšlení (thinking/reasoning), jinak spotřebuje všechny tokeny.")
        return json.loads(obsah)


def _klient(zakazka: Zakazka, log: Log):
    return _Offline(zakazka, log) if zakazka.prekladac == "offline" else _Codex(zakazka, log)


def _pouzij(vysledek: dict, podle_id: dict[int, Veta], zkraceni: bool) -> None:
    for x in vysledek.get("vety", []):
        v = podle_id.get(int(x.get("id", -1)))
        if v is not None and x.get("cs", "").strip() and not v.upraveno:
            v.cs = x["cs"].strip()
            v.vyslov = (x.get("vyslov") or "").strip() or v.cs
            if zkraceni:
                v.zkraceno = True


def preloz(zakazka: Zakazka, log: Log, prubeh: Callable[[float, str], None]) -> None:
    """Přeloží všechny věty bez ručně upraveného textu a přetečené dávkově zkrátí."""
    vety = [v for v in zakazka.vety if not v.upraveno]
    if not vety:
        return
    podle_id = {v.id: v for v in zakazka.vety}
    velikost = DAVKA_ZNAKU_OFFLINE if zakazka.prekladac == "offline" else DAVKA_ZNAKU_CODEX
    davky = _davky(vety, velikost)
    glosar: list[dict] = []
    with _klient(zakazka, log) as klient:
        for i, davka in enumerate(davky):
            prubeh(i / max(len(davky), 1), f"Překládám dávku {i + 1}/{len(davky)}…")
            prvni = davka[0].id
            kontext = [podle_id[prvni - k] for k in (3, 2, 1)
                       if prvni - k in podle_id and podle_id[prvni - k].cs]
            vysledek = klient.dotaz(_zadani(davka, kontext, glosar, zkratit=False))
            _pouzij(vysledek, podle_id, zkraceni=False)
            glosar = vysledek.get("glosar", glosar) or glosar
            zakazka.uloz()
        for kolo in range(2):
            dlouhe = [v for v in vety if len(v.vyslov or v.cs) > v.max_znaku * TOLERANCE]
            if not dlouhe:
                break
            prubeh(0.95, f"Zkracuji {len(dlouhe)} dlouhých vět (kolo {kolo + 1})…")
            for davka in _davky(dlouhe, velikost):
                _pouzij(klient.dotaz(_zadani(davka, [], glosar, zkratit=True)), podle_id, zkraceni=True)
            zakazka.uloz()
    chybi = [v.id for v in vety if not v.cs]
    if chybi:
        raise RuntimeError(f"Překlad chybí u vět {chybi[:10]}{'…' if len(chybi) > 10 else ''}")
    zakazka.hotovo("preklad")


def zkrat(zakazka: Zakazka, vety: list[Veta], log: Log) -> None:
    """Zkrácení podle skutečně změřené délky hlasu (volá se po prvním průchodu TTS)."""
    if not vety:
        return
    podle_id = {v.id: v for v in zakazka.vety}
    velikost = DAVKA_ZNAKU_OFFLINE if zakazka.prekladac == "offline" else DAVKA_ZNAKU_CODEX
    with _klient(zakazka, log) as klient:
        for davka in _davky(vety, velikost):
            _pouzij(klient.dotaz(_zadani(davka, [], [], zkratit=True)), podle_id, zkraceni=True)
    zakazka.uloz()
