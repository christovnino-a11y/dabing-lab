"""Překlad vět pro dabing s rozpočtem délky: Codex (výchozí) nebo offline LM Studio.

Codex dostává věty po dávkách (každý turn nese ~18k tokenů režie, takže po
jedné větě by to bylo drahé a pomalé). Každá věta má limit znaků odvozený z délky
původní řeči; co se nevejde, jde do dávkového zkrácení.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable

import requests

from . import titulky
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
MAX_POPIS_ZNAKU = 1500  # popis videa bývá dlouhý (odkazy, sponzoři); na jména stačí začátek

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


ROD_MLUVCIHO = {"m": "muž", "z": "žena"}


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
- Věty jsou dělené podle pauz řečníka, i uprostřed souvětí: věta bez koncové interpunkce (. ? !)
  pokračuje ve větě s id o 1 vyšším (téhož mluvčího). Takové navazující věty přelož jako jedno
  plynulé souvětí rozdělené na stejné části: každá česká část musí obsahově odpovídat své části
  originálu a vejít se do jejího času (řečník mezi nimi na obraze zmlkne, dabing má zmlknout s ním).
  Neslučuj je a obsah mezi nimi nepřesouvej, pokud to čeština snese. Pole `predchozi_cast`
  a `nasledujici_cast` ukazují sousední část souvětí, která v zadání není — jsou jen ke čtení:
  svou část uprav tak, aby na ně gramaticky navazovala, a nic z nich nepřebírej.
- Text originálu je automatický přepis řeči a může obsahovat špatně slyšená slova (např.
  „scream" místo „screen", „MetaQui" místo „Meta Quest 3", „2:49" místo „$249").
  Překládej podle zjevného smyslu v kontextu, ne podle chyby přepisu.
- Pole `video` (název, popis, druh titulků) použij pro správný zápis jmen, produktů a čísel.
  `en` je text originálu: z titulků od autora, pokud je video má, a se jmény a čísly, které už
  korektor ověřil na webu. Má-li věta navíc `prepis_stt`, je to náš původní strojový přepis téhož
  místa. Ověřené opravy jmen a čísel v `en` ber jako správné. Titulky od autora bývají přesnější,
  ale i ony můžou být strojové („Hug and Face" místo Hugging Face): kde se `en` a `prepis_stt` liší
  jinak, rozhodni podle smyslu, názvu a popisu videa. Titulky bývají zkrácené, obsah ber z obojího.
  Hranice vět v titulcích jsou jen přibližné — slovo navíc na začátku nebo konci věty, které patří
  k sousední větě, nepřekládej dvakrát.
- Jména, názvy produktů a firem nech tak, jak se běžně říkají. Stejný termín překládej vždy stejně
  (drž se glosáře, pokud ho dostaneš, a vrať ho doplněný).
- Čísla a jednotky piš slovy.
- Ke každé větě vrať i pole `vyslov`: stejný překlad, ale cizí jména, názvy a zkratky přepsané
  foneticky podle pravopisu cílového jazyka tak, jak je má dabér vyslovit{priklad}. Hlasový model
  čte text podle výslovnosti cílového jazyka, proto to potřebuje. V `cs` nech jména normálně
  (to jsou titulky).
  Pole se jmenuje `cs`, i když cílový jazyk není čeština — obsahuje překlad do cílového jazyka.
- Oslovení (tykání/vykání) drž konzistentní podle kontextu.
- Pole `pohlavi_mluvciho` (muž/žena) je pohlaví hlasu, kterým se věta namluví — změřené, ne hádané.
  V jazycích, kde se rod pozná na slovesech a přídavných jménech (čeština, slovenština, polština,
  ruština, němčina…), mluv o sobě v tomhle rodě: žena „řekla jsem", „byla jsem ráda", muž „řekl
  jsem", „byl jsem rád". Platí i pro oslovení a příčestí (např. „ukázala jsem", „zapomněl jsem").
  Když pole chybí, rod mluvčího neznáš: určuj ho z kontextu (jméno, oslovení druhými), jinak raději
  formulaci bez rodu (např. „mám rád" místo „byl jsem rád"). O druhých mluv v jejich rodě podle smyslu.
- Nic nepřidávej, nekomentuj a nepoužívej žádné nástroje ani soubory.
Vrať JSON podle schématu: pro každé id pole `cs` a `vyslov` a glosář použitých termínů."""


SCHEMA_OPRAVY = {
    "type": "object",
    "properties": {
        "opravy": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "puvodne": {"type": "string"},
                               "opraveno": {"type": "string"}, "duvod": {"type": "string"}},
                "required": ["id", "puvodne", "opraveno", "duvod"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["opravy"],
    "additionalProperties": False,
}
DAVKA_ZNAKU_OPRAVY = 12000

POKYNY_OPRAVY = """Jsi korektor automatického přepisu řeči (ASR) z videa. Dostaneš název a popis videa
a očíslované věty přepisu. ASR slyší zvuk dobře, ale neznámá jména píše foneticky („SLR Lexotica"
= EssilorLuxottica, „MetaQuI" = Meta Quest 3) a čísla zapisuje nejednoznačně („2:49" = $249,
mluvené „twelve ninety-nine" může být $12.99 i $1,299); občas splete i běžné slovo tak, že věta
nedává smysl („titles you can scream on Xbox" = stream).
Najdi vlastní jména, produkty, firmy, ceny a čísla, která jsou zkomolená nebo nejednoznačná,
a OVĚŘ je vyhledáváním na webu (oficiální zdroje, zpravodajství o téže události). Oprav jen to,
co je zjevně špatně slyšené a co máš ověřené; nic nepřeformulovávej, neopravuj gramatiku, styl,
zadrhnutí ani výplňová slova. `puvodne` musí být přesný úsek textu dané věty (přesně tak, jak je
napsaný), `opraveno` jeho správné znění, `duvod` stručně odkud to víš. Když si nejsi jistý,
neopravuj. Vrať JSON podle schématu (prázdný seznam, když není co opravit)."""


def _davky(vety: list[Veta], max_znaku: int) -> list[list[Veta]]:
    """Dávky do max_znaku; části jednoho souvětí (věta bez koncové interpunkce pokračuje v další)
    se nerozdělí do dvou dávek, dokud dávka nepřesáhne 1,5× limit."""
    davky, aktualni, znaky = [], [], 0
    for v in vety:
        rozdelene_souveti = bool(aktualni) and not aktualni[-1].en.rstrip().endswith(tuple(".?!…"))
        if aktualni and znaky + len(v.en) > max_znaku * (1.5 if rozdelene_souveti else 1.0):
            davky.append(aktualni)
            aktualni, znaky = [], 0
        aktualni.append(v)
        znaky += len(v.en) + 40
    if aktualni:
        davky.append(aktualni)
    return davky


UVODY = {
    "preklad": "Přelož tyto věty.\n",
    "zkratit": ("ZKRAŤ tyto překlady tak, aby se vešly do limitu znaků. Zachovej hlavní význam. "
                "Věta, která je jen částí souvětí (má `predchozi_cast` nebo `nasledujici_cast`), "
                "zůstane částí, která na sousedy navazuje.\n"),
    "prodlouzit": ("PRODLUŽ tyto překlady: jsou výrazně kratší, než jak dlouho řečník na obraze mluví, "
                   "a po české větě by zůstalo ticho. Přeformuluj je plněji a přirozeněji, jak by to "
                   "řekl dabér (bez zkratkovitosti; část souvětí jako přirozeně navazující část), "
                   "a přibliž se `cil_znaku`. Nepřidávej nové informace ani vatu a nepřekroč "
                   "`limit_znaku`.\n"),
}


def _video(zakazka: Zakazka, druh_titulku: str) -> dict:
    popis = " ".join(re.sub(r"https?://\S+", " ", zakazka.popis).split())[:MAX_POPIS_ZNAKU]
    return {"nazev": zakazka.nazev, **({"popis": popis} if popis else {}),
            **({"titulky": druh_titulku} if druh_titulku else {})}


KONCE_VET = tuple(".?!…")


def _sousedi(v: Veta, podle_id: dict[int, Veta], v_davce: set[int]) -> dict:
    """Sousední části téhož souvětí, které v dávce nejsou (zkracování, prodlužování a dopřeklad
    posílají jen vybrané věty) — ke čtení, aby česká část gramaticky navazovala."""
    pred, dal = podle_id.get(v.id - 1), podle_id.get(v.id + 1)
    vysledek = {}
    if (pred and pred.id not in v_davce and pred.mluvci == v.mluvci
            and not pred.en.rstrip().endswith(KONCE_VET)):
        vysledek["predchozi_cast"] = {"en": pred.en_preklad, "cs": pred.cs}
    if (dal and dal.id not in v_davce and dal.mluvci == v.mluvci
            and not v.en.rstrip().endswith(KONCE_VET)):
        vysledek["nasledujici_cast"] = {"en": dal.en_preklad, "cs": dal.cs}
    return vysledek


def _zadani(davka: list[Veta], kontext: list[Veta], glosar: list[dict], rezim: str = "preklad",
            video: dict | None = None, vsechny: list[Veta] | None = None) -> str:
    podle_id = {v.id: v for v in vsechny or []}
    v_davce = {v.id for v in davka}

    def veta(v: Veta) -> dict:
        zaklad = {"id": v.id, "mluvci": f"M{v.mluvci + 1}", "en": v.en_preklad,
                  "originál_trvá_s": round(v.delka, 1), **_sousedi(v, podle_id, v_davce)}
        if v.pohlavi in ROD_MLUVCIHO:
            zaklad["pohlavi_mluvciho"] = ROD_MLUVCIHO[v.pohlavi]
        if v.en_preklad != v.en:
            zaklad["prepis_stt"] = v.en
        if v.prirozena_s and rezim != "preklad":
            zaklad["dabing_teď_trvá_s"] = round(v.prirozena_s, 1)  # změřeno z vygenerovaného hlasu
        if rezim == "preklad":
            return {**zaklad, "cil_znaku": v.cil_znaku, "limit_znaku": v.max_znaku}
        oprava = {"soucasny_preklad": v.cs, "soucasna_vyslovnost": v.vyslov or v.cs,
                  "limit_znaku": v.max_znaku}
        return {**zaklad, **oprava, **({"cil_znaku": v.cil_znaku} if rezim == "prodlouzit" else {})}

    data = {**({"video": video} if video else {}),
            "kontext_predchozich_vet": [{"en": v.en_preklad, "cs": v.cs} for v in kontext],
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

    def dotaz(self, zadani: str, schema: dict = SCHEMA_PREKLAD, vlastni_pokyny: str | None = None) -> dict:
        r = self.codex.turn([text(zadani)], schema=schema,
                            pokyny=vlastni_pokyny or pokyny(self.zakazka.jazyk, self.zakazka.jazyk_zdroje))
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


CISLOVKY = re.compile(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                      r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
                      r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b", re.I)


def _duveryhodna(puvodne: str, opraveno: str) -> bool:
    """Pojistka proti „opravám" z webu, které přidávají, co nezaznělo. Test 2026-09-28 (Meta
    Connect): Codex správně opravil Zena, EssilorLuxottica, $249, Meta Quest 3, stream…, ale
    „that hands first title" přepsal na „75 hand-first titles" a „most the most" gramaticky."""
    cislice_p, cislice_o = re.sub(r"\D", "", puvodne), re.sub(r"\D", "", opraveno)
    if CISLOVKY.search(puvodne) and cislice_o:
        return True  # číslovka slovy → číslicemi („twelve ninety-nine" → „$1,299")
    if cislice_o != cislice_p:
        if cislice_p and not cislice_o.startswith(cislice_p):
            return False  # jiné číslo, než zaznělo ($12.99 → $1,299.99 projde, 249 → 299 ne)
        if not cislice_p and not re.search(r"[A-Z]", opraveno):
            return False  # nové číslo mimo název produktu („Meta Quest 3" projde, „75 titles" ne)
    podobnost = difflib.SequenceMatcher(a=puvodne.lower(), b=opraveno.lower()).ratio()
    if re.search(r"[A-Z0-9$€£+]", opraveno):
        return podobnost >= 0.3  # jméno, produkt, cena — ale ne úplně jiné slovo
    # běžné slovo: jen záměna podobně znějícího (scream → stream), nic nedoplňovat
    return len(puvodne.split()) == len(opraveno.split()) and podobnost >= 0.6


def _over_jmena(klient: "_Codex", zakazka: Zakazka, log: Log, video: dict,
                prubeh: Callable[[float, str], None]) -> int:
    """Codex s vyhledáváním na webu opraví v textu originálu zkomolená jména, produkty a čísla
    (Veta.en_opraveno). Selhání překlad nezastaví. Vrací počet oprav."""
    for v in zakazka.vety:
        v.en_opraveno = ""
    opravy: list[dict] = []
    try:
        for davka in _davky(zakazka.vety, DAVKA_ZNAKU_OPRAVY):
            prubeh(0.0, "Ověřuji jména a čísla na webu…")
            data = {"video": video, "vety": [{"id": v.id, "text": v.en_preklad} for v in davka]}
            vysledek = klient.dotaz("Oprav špatně slyšená jména a čísla v těchto větách.\n"
                                    + json.dumps(data, ensure_ascii=False),
                                    schema=SCHEMA_OPRAVY, vlastni_pokyny=POKYNY_OPRAVY)
            opravy += vysledek.get("opravy", [])
    except (RuntimeError, ValueError) as chyba:
        log(f"Ověření jmen na webu selhalo — překládám bez něj ({chyba}).")
        return 0
    podle_id = {v.id: v for v in zakazka.vety}
    provedene: list[str] = []
    odmitnute: list[str] = []
    for o in opravy:
        v = podle_id.get(int(o.get("id", -1)))
        puvodne, opraveno = (o.get("puvodne") or "").strip(), (o.get("opraveno") or "").strip()
        if v is None or not puvodne or not opraveno or puvodne == opraveno or puvodne not in v.en_preklad:
            continue  # oprava, která nesedí na text věty, by mohla přepsat něco jiného
        if not _duveryhodna(puvodne, opraveno):
            odmitnute.append(f"{puvodne} → {opraveno}")
            continue
        v.en_opraveno = v.en_preklad.replace(puvodne, opraveno, 1)
        provedene.append(f"{puvodne} → {opraveno}")
    if provedene:
        log(f"Codex ověřil na webu a opravil {len(provedene)} míst: " + "; ".join(provedene[:15])
            + ("…" if len(provedene) > 15 else ""))
    else:
        log("Codex ověřil jména a čísla na webu — nic k opravě.")
    if odmitnute:
        log(f"Odmítnuté opravy (přidávaly, co nezaznělo): {'; '.join(odmitnute[:8])}")
    zakazka.uloz()
    return len(provedene)


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
    druh_titulku = titulky.druh(zakazka)
    video = _video(zakazka, druh_titulku)
    lisi = titulky.prirad(zakazka)  # bez titulků smaže i staré en_titulky
    if druh_titulku:
        log(f"Text vět beru z titulků od autora videa; od našeho přepisu se liší u {lisi} vět "
            "— překladač uvidí obojí. Časy zůstávají z přepisu.")
    with _klient(zakazka, log) as klient:
        if isinstance(klient, _Codex):  # LM Studio web nemá
            _over_jmena(klient, zakazka, log, video, prubeh)
        for i, davka in enumerate(davky):
            prubeh(i / max(len(davky), 1), f"Překládám dávku {i + 1}/{len(davky)}…")
            prvni = davka[0].id
            kontext = [podle_id[prvni - k] for k in (3, 2, 1)
                       if prvni - k in podle_id and podle_id[prvni - k].cs]
            vysledek = klient.dotaz(_zadani(davka, kontext, glosar, video=video))
            _pouzij(vysledek, podle_id, zkraceni=False)
            glosar = vysledek.get("glosar", glosar) or glosar
            zakazka.uloz()
        chybi = [v for v in vety if not v.cs]
        if chybi:  # Codex občas větu vynechá (hlavně samotné „Um") — jeden pokus navíc
            prubeh(0.9, f"Dopřekládám {len(chybi)} vynechaných vět…")
            for davka in _davky(chybi, velikost):
                _pouzij(klient.dotaz(_zadani(davka, [], glosar, video=video, vsechny=zakazka.vety)), podle_id, zkraceni=False)
            zakazka.uloz()
        for kolo in range(2):
            dlouhe = [v for v in vety if len(v.vyslov or v.cs) > v.max_znaku * TOLERANCE]
            if not dlouhe:
                break
            prubeh(0.95, f"Zkracuji {len(dlouhe)} dlouhých vět (kolo {kolo + 1})…")
            for davka in _davky(dlouhe, velikost):
                _pouzij(klient.dotaz(_zadani(davka, [], glosar, "zkratit", vsechny=zakazka.vety)), podle_id, zkraceni=True)
            zakazka.uloz()
        _prodluz(klient, zakazka, vety, glosar, prubeh)
    vyplne = [v for v in vety if not v.cs and _jen_vypln(v.en)]
    chybi = [v.id for v in vety if not v.cs and not _jen_vypln(v.en)]
    if chybi:
        raise RuntimeError(f"Překlad chybí u vět {chybi[:10]}{'…' if len(chybi) > 10 else ''}")
    if vyplne:  # mix je nechá bez dabingu (qa bez_dabingu): zní originál, „um" zní v každém jazyce stejně
        log(f"{len(vyplne)} vět je jen výplň („{vyplne[0].en}“) — nedabuje se, zní originál.")
    zakazka.hotovo("preklad")


VYPLNE = {"um", "uh", "uhm", "umm", "hmm", "mm", "ah", "oh", "er", "eh", "ehm"}


def _jen_vypln(text: str) -> bool:
    slova = titulky.normuj(text).split()
    return bool(slova) and all(s in VYPLNE for s in slova)


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
        _pouzij(klient.dotaz(_zadani(davka, [], glosar, "prodlouzit", vsechny=zakazka.vety)), podle_id,
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
                _pouzij(klient.dotaz(_zadani(davka, [], [], "prodlouzit", vsechny=zakazka.vety)), podle_id,
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
            _pouzij(klient.dotaz(_zadani(davka, [], [], "zkratit", vsechny=zakazka.vety)), podle_id, zkraceni=True)
    zakazka.uloz()
