"""Lokální překladače: Ollama a LM Studio na tomto Macu (nic neodchází ven).

Tenká vrstva nad HTTP API obou serverů: zjištění stavu (je nainstalováno, běží, jaké má
modely), spuštění serveru a jeden strukturovaný dotaz (JSON podle schématu).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests

VYCHOZI = {"ollama": "http://127.0.0.1:11434", "lmstudio": "http://127.0.0.1:1234"}
SLUZBY = {k: os.environ.get(f"DABING_{k.upper()}_URL", v) for k, v in VYCHOZI.items()}
NAZVY = {"ollama": "Ollama", "lmstudio": "LM Studio"}
CASOVY_LIMIT_STAVU = 2
CASOVY_LIMIT_DOTAZU = 900
NUM_CTX = 16384
MAX_TOKENU = 12000


def _url(sluzba: str) -> str:
    """Adresa API: proměnná prostředí DABING_*_URL, jinak adresa uložená v nastavení, jinak výchozí.
    Čte se při každém volání, aby šlo adresu změnit za běhu (a v testech); do job.json se nepíše."""
    from . import nastaveni  # nastaveni importuje lokalni
    env = {"ollama": "DABING_OLLAMA_URL", "lmstudio": "DABING_LMSTUDIO_URL"}[sluzba]
    return (os.environ.get(env) or nastaveni.ulozena_adresa(sluzba) or VYCHOZI[sluzba]).rstrip("/")


def je_na_tomto_macu(sluzba: str) -> bool:
    return (urlparse(_url(sluzba)).hostname or "") in ("127.0.0.1", "localhost", "::1")


@dataclass
class Stav:
    nainstalovano: bool
    bezi: bool
    modely: list[str] = field(default_factory=list)
    popis: str = ""


def _nainstalovano(sluzba: str) -> bool:
    if sluzba == "ollama":
        return bool(shutil.which("ollama") or Path("/opt/homebrew/bin/ollama").exists()
                    or Path("/Applications/Ollama.app").exists())
    return bool((Path.home() / ".lmstudio/bin/lms").exists() or Path("/Applications/LM Studio.app").exists()
                or shutil.which("lms"))


def _modely(sluzba: str) -> list[str]:
    """Jazykové modely, které server nabízí (vyhodí výjimku, když neběží)."""
    if sluzba == "ollama":
        data = requests.get(f"{_url(sluzba)}/api/tags", timeout=CASOVY_LIMIT_STAVU).json()
        jmena = [m.get("name") or m.get("model") or "" for m in data.get("models", [])]
        # cloudové modely Ollamy běží mimo Mac, lokální volba slibuje, že nic neodchází
        jmena = [j for j in jmena if not j.lower().endswith("cloud")]
    else:
        data = requests.get(f"{_url(sluzba)}/v1/models", timeout=CASOVY_LIMIT_STAVU).json()
        jmena = [m.get("id") or "" for m in data.get("data", [])]
    return [j for j in jmena if j and "embed" not in j.lower()]


def stav(sluzba: str) -> Stav:
    nazev = NAZVY[sluzba]
    instalace = _nainstalovano(sluzba)
    na_macu = je_na_tomto_macu(sluzba)
    try:
        modely = _modely(sluzba)
        bezi = True
    except (requests.RequestException, ValueError):
        modely, bezi = [], False
    if not bezi:
        if not na_macu:
            return Stav(False, False, [], f"{nazev} na adrese {_url(sluzba)} neodpovídá — "
                                          "zkontroluj adresu a že tam server běží.")
        if not instalace:
            navod = "`brew install ollama`" if sluzba == "ollama" else "stáhni z [lmstudio.ai](https://lmstudio.ai)"
            return Stav(False, False, [], f"{nazev} není nainstalovaný — {navod}.")
        return Stav(True, False, [], f"{nazev} neběží — klikni na „Zapnout server“.")
    if not modely:
        if sluzba == "ollama":
            return Stav(True, True, [], "Stáhni si v Ollamě model podle paměti svého Macu (`ollama pull <model>`).")
        return Stav(True, True, [], "Stáhni si v LM Studiu model podle paměti svého Macu.")
    n = len(modely)
    slovo = "model" if n == 1 else ("modely" if n < 5 else "modelů")
    return Stav(True, True, modely, f"Připraveno, {n} {slovo}.")


def _bezi(sluzba: str) -> bool:
    try:
        _modely(sluzba)
        return True
    except (requests.RequestException, ValueError):
        return False


def zapni(sluzba: str) -> str:
    """Spustí server odpojeně od labu a počká (až ~10 s), než odpoví."""
    nazev = NAZVY[sluzba]
    if _bezi(sluzba):
        return f"{nazev} už běží."
    if not je_na_tomto_macu(sluzba):
        return f"{nazev} na adrese {_url(sluzba)} neběží — spusť ho na tom počítači."
    if sluzba == "ollama":
        if Path("/Applications/Ollama.app").exists():
            prikaz = ["open", "-a", "Ollama"]
        else:
            program = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
            prikaz = [program, "serve"]
    else:
        lms = Path.home() / ".lmstudio/bin/lms"
        program = str(lms) if lms.exists() else shutil.which("lms")
        if not program:
            return "Nenašel jsem příkaz `lms` — spusť server v aplikaci LM Studio (Developer → Start Server)."
        prikaz = [program, "server", "start"]
    try:
        subprocess.Popen(prikaz, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as chyba:
        return f"{nazev} se nepodařilo spustit: {chyba}"
    konec = time.monotonic() + 10
    while time.monotonic() < konec:
        if _bezi(sluzba):
            return f"{nazev} běží."
        time.sleep(0.5)
    return f"{nazev} se spouští, ale zatím neodpovídá — za chvíli klikni na „Znovu zkontrolovat“."


# ------------------------------------------------------------------ dotaz
def _json(obsah: str, schema: dict) -> dict:
    """Tolerantní parsování: ořízne ```json ohrady a text kolem objektu. Výsledek musí být objekt
    s povinnými poli schématu, jinak ValueError (holé pole nebo kus z přemýšlení není překlad)."""
    t = (obsah or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        data = json.loads(t)
    except ValueError:
        zacatek, konec = t.find("{"), t.rfind("}")
        if not 0 <= zacatek < konec:
            raise
        data = json.loads(t[zacatek:konec + 1])
    if not isinstance(data, dict) or any(k not in data for k in schema.get("required", ())):
        raise ValueError("odpověď neodpovídá schématu")
    return data


def _text_zpravy(zprava: dict | None) -> str:
    """Text odpovědi. Modely s přemýšlením dávají v LM Studiu i Ollamě odpověď podle schématu
    někdy jen do pole přemýšlení a `content` nechají prázdné (LM Studio 2026-11-06)."""
    zprava = zprava or {}
    return (zprava.get("content") or zprava.get("reasoning_content") or zprava.get("thinking") or "")


def _prazdna(sluzba: str) -> RuntimeError:
    return RuntimeError("Model vrátil prázdnou odpověď. Vypni v něm přemýšlení (thinking/reasoning), "
                        "jinak spotřebuje všechny tokeny.")


def _necitelna() -> RuntimeError:
    return RuntimeError("Model nevrátil použitelnou odpověď (useknutý nebo špatný JSON). "
                        "Zkus jiný model.")


def _spojeni(sluzba: str, chyba: Exception) -> RuntimeError:
    nazev = NAZVY[sluzba]
    if sluzba == "lmstudio":
        return RuntimeError("LM Studio neběží. Spusť v něm server (Developer → Start Server) "
                            f"a načti model. ({type(chyba).__name__})")
    return RuntimeError(f"{nazev} neběží — klikni v labu na „Zapnout server“ nebo spusť `ollama serve`. "
                        f"({type(chyba).__name__})")


def _post(sluzba: str, cesta: str, telo: dict, model: str) -> requests.Response:
    try:
        odpoved = requests.post(f"{_url(sluzba)}{cesta}", json=telo, timeout=CASOVY_LIMIT_DOTAZU)
    except requests.ReadTimeout as chyba:
        raise RuntimeError(f"Model „{model}“ neodpověděl do {CASOVY_LIMIT_DOTAZU // 60} minut. "
                           "Zkus menší model.") from chyba
    except requests.RequestException as chyba:
        raise _spojeni(sluzba, chyba) from chyba
    if odpoved.status_code == 404:
        raise RuntimeError(f"{NAZVY[sluzba]} nezná model „{model}“ — vyber jiný, nebo ho stáhni"
                           + (f" (`ollama pull {model}`)." if sluzba == "ollama" else "."))
    return odpoved


def _lmstudio(model: str, system: str, zadani: str, schema: dict) -> dict:
    odpoved = _post("lmstudio", "/v1/chat/completions", {
        "model": model,
        "temperature": 0.3,
        "max_tokens": MAX_TOKENU,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": zadani}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "preklad", "strict": True, "schema": schema}},
    }, model)
    try:
        data = odpoved.json()
    except ValueError:
        raise RuntimeError(f"LM Studio vrátilo chybu {odpoved.status_code}: {odpoved.text[:300]}") from None
    if "error" in data and not data.get("choices"):
        raise RuntimeError(f"LM Studio: {data['error'] if isinstance(data['error'], str) else data['error'].get('message', data['error'])}")
    obsah = _text_zpravy((data.get("choices") or [{}])[0].get("message"))
    if not obsah.strip():
        raise _prazdna("lmstudio")
    try:
        return _json(obsah, schema)
    except ValueError:
        raise _necitelna() from None


def _ollama(model: str, system: str, zadani: str, schema: dict) -> dict:
    """Nativní /api/chat: OpenAI-kompatibilní cesta ignoruje num_ctx (tiše ořízne na 4096) i think.

    Některé modely s `think:false` rozbijí `format`, jiné bez přemýšlení pole `think` odmítnou,
    proto při 400, prázdné nebo nečitelné odpovědi: znovu bez `think`, pak bez `format`."""
    pokusy = [{"think": False, "format": True}, {"format": True}, {}]
    posledni: RuntimeError | None = None
    for pokus in pokusy:
        zprava_system = system
        telo = {"model": model, "stream": False,
                "options": {"temperature": 0.3, "num_ctx": NUM_CTX, "num_predict": MAX_TOKENU}}
        if "think" in pokus:
            telo["think"] = pokus["think"]
        if pokus.get("format"):
            telo["format"] = schema
        else:
            zprava_system += ("\n\nOdpověz jen JSON podle schématu: "
                              + json.dumps(schema, ensure_ascii=False))
        telo["messages"] = [{"role": "system", "content": zprava_system},
                            {"role": "user", "content": zadani}]
        odpoved = _post("ollama", "/api/chat", telo, model)
        if odpoved.status_code == 400:
            posledni = RuntimeError(f"Ollama odmítla dotaz (400): {odpoved.text[:300]}")
            continue
        if odpoved.status_code >= 400:
            raise RuntimeError(f"Ollama vrátila chybu {odpoved.status_code}: {odpoved.text[:300]}")
        try:
            obsah = _text_zpravy(odpoved.json().get("message"))
        except ValueError:
            obsah = ""
        try:
            if not obsah.strip():
                raise ValueError
            return _json(obsah, schema)
        except ValueError:
            posledni = _necitelna() if obsah.strip() else _prazdna("ollama")
    raise posledni or _prazdna("ollama")


def dotaz(sluzba: str, model: str, system: str, zadani: str, schema: dict) -> dict:
    if not model:
        raise RuntimeError(f"Není vybraný žádný model {NAZVY[sluzba]}.")
    return (_ollama if sluzba == "ollama" else _lmstudio)(model, system, zadani, schema)
