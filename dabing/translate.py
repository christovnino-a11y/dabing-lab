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
from .jazyky import DABING, ORIGINAL
from .job import Veta, Zakazka

Log = Callable[[str], None]

NAZVY_JAZYKU = {kod: j.do_jazyka for kod, j in DABING.items()}
ZE_JAZYKA = {kod: odkud for kod, (_, odkud) in ORIGINAL.items()}
DAVKA_ZNAKU_CODEX = 6000
DAVKA_ZNAKU_OFFLINE = 2500
TOLERANCE = 1.05
# Věta kratší než tento podíl cíle se nechá přeformulovat plněji (jinak po ní zůstane díra,
# zatímco řečník na obraze ještě mluví). Krátké věty pod MIN_CIL_PRODLOUZENI se neřeší.
PRAH_KRATKEHO_PREKLADU = 0.8
MIN_CIL_PRODLOUZENI = 25
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
    priklad = (' (např. „Muse" → „Mjúz", „VM" → „ví em", „AI" → „éj áj", „Meta" → „Meta")'
               if jazyk == "cs" else "")
    return f"""Jsi zkušený dabingový překladatel {odkud} do {cilovy}.
Dostaneš očíslované věty z videa (mluvčí, text originálu v poli `en` a limity znaků). Pravidla:
- Každou větu přelož do přirozené mluvené řeči, jak by ji řekl dabér. Věrně významem, ne doslovně.
- Délka: každá věta má `originál_trvá_s`, `cil_znaku` (tolik znaků změřený hlas řekne za čas,
  kdy mluví originál) a `limit_znaku` (strop). Při úpravách dostaneš i `dabing_teď_trvá_s`
  (skutečně změřená délka českého hlasu). Přibliž se cíli — překládej úplně a věrně, nic zbytečně nevynechávej a nepřekládej
  úsporněji, než je cíl: když originál mluví pomalu, zvol plnější, přirozenou formulaci (ne vatu
  ani nové informace), ať po větě nezůstane ticho, zatímco řečník na obraze ještě mluví. Limit
  nikdy nepřekračuj; když se obsah nevejde, zkrať vatu a opakování.
- Text originálu je automatický přepis řeči a může obsahovat špatně slyšená slova (např.
  „scream" místo „screen", „MetaQui" místo „Meta Quest 3", „2:49" místo „$249").
  Překládej podle zjevného smyslu v kontextu, ne podle chyby přepisu.
- Jména, názvy produktů a firem nech tak, jak se běžně říkají. Stejný termín překládej vždy stejně
  (drž se glosáře, pokud ho dostaneš, a vrať ho doplněný).
- Čísla a jednotky piš slovy.
- Ke každé větě vrať i pole `vyslov`: stejný překlad, ale cizí jména, názvy a zkratky přepsané
  foneticky podle pravopisu cílového jazyka tak, jak je má dabér vyslovit{priklad}. Hlasový model
  čte text podle výslovnosti cílového jazyka, proto to potřebuje. V `cs` nech jména normálně
  (to jsou titulky).
  Pole se jmenuje `cs`, i když cílový jazyk není čeština — obsahuje překlad do cílového jazyka.
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


UVODY = {
    "preklad": "Přelož tyto věty.\n",
    "zkratit": "ZKRAŤ tyto překlady tak, aby se vešly do limitu znaků. Zachovej hlavní význam.\n",
    "prodlouzit": ("PRODLUŽ tyto překlady: jsou výrazně kratší, než jak dlouho řečník na obraze mluví, "
                   "a po české větě by zůstalo ticho. Přeformuluj je plněji a přirozeněji, jak by to "
                   "řekl dabér (celými větami, bez zkratkovitosti), a přibliž se `cil_znaku`. "
                   "Nepřidávej nové informace ani vatu a nepřekroč `limit_znaku`.\n"),
}


def _zadani(davka: list[Veta], kontext: list[Veta], glosar: list[dict], rezim: str = "preklad") -> str:
    def veta(v: Veta) -> dict:
        zaklad = {"id": v.id, "mluvci": f"M{v.mluvci + 1}", "en": v.en,
                  "originál_trvá_s": round(v.delka, 1)}
        if v.prirozena_s and rezim != "preklad":
            zaklad["dabing_teď_trvá_s"] = round(v.prirozena_s, 1)  # změřeno z vygenerovaného hlasu
        if rezim == "preklad":
            return {**zaklad, "cil_znaku": v.cil_znaku, "limit_znaku": v.max_znaku}
        oprava = {"soucasny_preklad": v.cs, "soucasna_vyslovnost": v.vyslov or v.cs,
                  "limit_znaku": v.max_znaku}
        return {**zaklad, **oprava, **({"cil_znaku": v.cil_znaku} if rezim == "prodlouzit" else {})}

    data = {"kontext_predchozich_vet": [{"en": v.en, "cs": v.cs} for v in kontext],
            "glosar": glosar, "vety": [veta(v) for v in davka]}
    return UVODY[rezim] + json.dumps(data, ensure_ascii=False)


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


def _pouzij(vysledek: dict, podle_id: dict[int, Veta], zkraceni: bool,
            jen_delsi: bool = False) -> None:
    for x in vysledek.get("vety", []):
        v = podle_id.get(int(x.get("id", -1)))
        if v is not None and x.get("cs", "").strip() and not v.upraveno:
            if jen_delsi:  # prodloužení bereme, jen když je opravdu delší a vejde se
                novy = len((x.get("vyslov") or x["cs"]).strip())
                if not len(v.vyslov or v.cs) < novy <= v.max_znaku * TOLERANCE:
                    continue
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
            vysledek = klient.dotaz(_zadani(davka, kontext, glosar))
            _pouzij(vysledek, podle_id, zkraceni=False)
            glosar = vysledek.get("glosar", glosar) or glosar
            zakazka.uloz()
        for kolo in range(2):
            dlouhe = [v for v in vety if len(v.vyslov or v.cs) > v.max_znaku * TOLERANCE]
            if not dlouhe:
                break
            prubeh(0.95, f"Zkracuji {len(dlouhe)} dlouhých vět (kolo {kolo + 1})…")
            for davka in _davky(dlouhe, velikost):
                _pouzij(klient.dotaz(_zadani(davka, [], glosar, "zkratit")), podle_id, zkraceni=True)
            zakazka.uloz()
        _prodluz(klient, zakazka, vety, glosar, prubeh)
    chybi = [v.id for v in vety if not v.cs]
    if chybi:
        raise RuntimeError(f"Překlad chybí u vět {chybi[:10]}{'…' if len(chybi) > 10 else ''}")
    zakazka.hotovo("preklad")


def _kratke(vety: list[Veta]) -> list[Veta]:
    return [v for v in vety if not v.upraveno and v.cs and v.cil_znaku >= MIN_CIL_PRODLOUZENI
            and len(v.vyslov or v.cs) < v.cil_znaku * PRAH_KRATKEHO_PREKLADU]


def _prodluz(klient, zakazka: Zakazka, vety: list[Veta], glosar: list[dict],
             prubeh: Callable[[float, str], None]) -> int:
    kratke = _kratke(vety)
    if not kratke:
        return 0
    prubeh(0.97, f"Prodlužuji {len(kratke)} příliš krátkých vět…")
    podle_id = {v.id: v for v in zakazka.vety}
    pred = {v.id: v.vyslov or v.cs for v in kratke}
    velikost = DAVKA_ZNAKU_OFFLINE if zakazka.prekladac == "offline" else DAVKA_ZNAKU_CODEX
    for davka in _davky(kratke, velikost):
        _pouzij(klient.dotaz(_zadani(davka, [], glosar, "prodlouzit")), podle_id,
                zkraceni=False, jen_delsi=True)
    zakazka.uloz()
    return sum((v.vyslov or v.cs) != pred[v.id] for v in kratke)


def dolad_delky(zakazka: Zakazka, log: Log, prubeh: Callable[[float, str], None]) -> None:
    """U hotového překladu: příliš krátké věty (po nich by zůstalo ticho) nechá přeformulovat
    plněji. Bez krátkých vět nic nevolá. Když překladač není k dispozici, jen varuje."""
    if not _kratke(zakazka.vety):
        return
    try:
        with _klient(zakazka, log) as klient:
            zmeneno = _prodluz(klient, zakazka, zakazka.vety, [], prubeh)
        log(f"Prodlouženo {zmeneno} příliš krátkých vět.")
    except RuntimeError as chyba:
        log(f"Pozor: krátké věty se nepodařilo prodloužit ({chyba}) — po některých zůstane ticho.")


def prodluz(zakazka: Zakazka, vety: list[Veta], log: Log) -> None:
    """Prodloužení podle skutečně změřené délky hlasu (volá se po prvním průchodu TTS).
    Selhání překladače dabing nezastaví — věta jen zůstane kratší."""
    if not vety:
        return
    podle_id = {v.id: v for v in zakazka.vety}
    velikost = DAVKA_ZNAKU_OFFLINE if zakazka.prekladac == "offline" else DAVKA_ZNAKU_CODEX
    try:
        with _klient(zakazka, log) as klient:
            for davka in _davky(vety, velikost):
                _pouzij(klient.dotaz(_zadani(davka, [], [], "prodlouzit")), podle_id,
                        zkraceni=False, jen_delsi=True)
    except RuntimeError as chyba:
        log(f"Pozor: krátké věty se nepodařilo prodloužit ({chyba}).")
    zakazka.uloz()


def zkrat(zakazka: Zakazka, vety: list[Veta], log: Log) -> None:
    """Zkrácení podle skutečně změřené délky hlasu (volá se po prvním průchodu TTS)."""
    if not vety:
        return
    podle_id = {v.id: v for v in zakazka.vety}
    velikost = DAVKA_ZNAKU_OFFLINE if zakazka.prekladac == "offline" else DAVKA_ZNAKU_CODEX
    with _klient(zakazka, log) as klient:
        for davka in _davky(vety, velikost):
            _pouzij(klient.dotaz(_zadani(davka, [], [], "zkratit")), podle_id, zkraceni=True)
    zakazka.uloz()
