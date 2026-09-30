"""Přihlášení k Codexu (účet ChatGPT) přímo v labu — přes `codex app-server`.

Lab překládá jen přes předplatné ChatGPT (API klíč odmítne `codex_client`), proto se tu
přihlašuje jen účtem ChatGPT: kódem zařízení (odkaz + kód, funguje i z jiného zařízení)
nebo v prohlížeči (OpenAI se vrací na localhost, prohlížeč musí běžet na tomto počítači).

Přihlášení se uloží do `codex_home/auth.json` — v .gitignore, do repozitáře nikdy nejde.
Když je `auth.json` odkaz (symlink) na přihlášení Codex CLI (`~/.codex/auth.json`), lab ho
sdílí. Odhlášení v labu pak jen zruší odkaz: `account/logout` by mohl zneplatnit tokeny
sdíleného přihlášení a odhlásit i Codex CLI. Nové přihlášení v labu odkaz nejdřív odloží
stranou (přesune se odkaz, ne přihlášení CLI), aby se přihlášení CLI nikdy nepřepsalo; když
se přihlášení nepovede, je zrušené nebo vyprší, odkaz se vrátí.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from openai_codex.api import Codex
from openai_codex.client import CodexConfig

from .codex_client import _KLICE_API, CODEX_CWD, CODEX_HOME

AUTH = CODEX_HOME / "auth.json"
ODLOZENY_ODKAZ = CODEX_HOME / "auth.json.odkaz-cli"  # odkaz na přihlášení CLI během pokusu
CEKANI_S = 15 * 60  # jak dlouho nejvýš čekat, než uživatel přihlášení dokončí

_zamek = threading.Lock()
_start = threading.Lock()  # zrušení předchozího + spuštění nového pokusu jako jeden krok
_rozpracovane: dict[str, "Prihlaseni | None"] = {"pokus": None}


def _konfigurace() -> CodexConfig:
    for klic in _KLICE_API:  # žádný API klíč z prostředí — jen účet ChatGPT
        os.environ.pop(klic, None)
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    CODEX_CWD.mkdir(parents=True, exist_ok=True)
    return CodexConfig(cwd=str(CODEX_CWD), env={"CODEX_HOME": str(CODEX_HOME)},
                       client_name="dabing_lab", client_title="Dabing lab")


def sdileny() -> bool:
    """`auth.json` je odkaz na přihlášení jinde (typicky Codex CLI)."""
    return AUTH.is_symlink()


def _odloz_odkaz() -> None:
    if sdileny():
        ODLOZENY_ODKAZ.unlink(missing_ok=True)
        os.replace(AUTH, ODLOZENY_ODKAZ)  # přesune se odkaz, ne soubor, na který ukazuje


def _vrat_odkaz() -> None:
    """Odložený odkaz na přihlášení CLI zpátky — jen když mezitím nevzniklo vlastní přihlášení."""
    if ODLOZENY_ODKAZ.is_symlink() and not AUTH.exists() and not AUTH.is_symlink():
        os.replace(ODLOZENY_ODKAZ, AUTH)


@dataclass
class Stav:
    prihlasen: bool      # přihlášen účtem ChatGPT (jediné, čím lab překládá)
    popis: str           # pro UI
    overeno: bool = True  # False = stav se nepodařilo zjistit (to není totéž co „nepřihlášen")


def stav() -> Stav:
    """Kdo je přihlášený (`account/read`, bez obnovy tokenu)."""
    with _zamek:
        if _rozpracovane["pokus"] is None:
            _vrat_odkaz()  # po restartu labu uprostřed přihlašování
    try:
        with Codex(config=_konfigurace()) as codex:
            odpoved = codex.account()
    except Exception as chyba:  # noqa: BLE001 — stav se jen ukáže
        return Stav(False, f"Stav účtu se nepodařilo zjistit: {type(chyba).__name__}: {chyba}", overeno=False)
    ucet = getattr(odpoved.account, "root", odpoved.account)
    druh = getattr(ucet, "type", None)
    if druh == "chatgpt":
        plan = getattr(getattr(ucet, "plan_type", None), "value", None) or "?"
        kdo = ucet.email or "účet bez e-mailu"
        odkud = " — sdílené s Codex CLI" if sdileny() else ""
        return Stav(True, f"Přihlášen účtem ChatGPT **{kdo}** (plán {plan}){odkud}.")
    if druh is not None:
        return Stav(False, f"Codex je přihlášený jinak než účtem ChatGPT ({druh}) — lab překládá jen "
                           "přes předplatné ChatGPT. Odhlas se a přihlas účtem ChatGPT.")
    return Stav(False, "Nepřihlášen — překlad Codexem nepůjde. Přihlas se účtem ChatGPT.")


class Prihlaseni:
    """Jeden rozpracovaný pokus o přihlášení. Na dokončení čeká vlákno na pozadí."""

    def __init__(self, druh: str):
        self.chyba: str | None = None
        self.uspech = False
        self._hotovo = threading.Event()
        self._uklizeno = False
        _odloz_odkaz()  # nové přihlášení nesmí zapsat přes odkaz do přihlášení Codex CLI
        try:
            self.codex = Codex(config=_konfigurace())
        except Exception:
            _vrat_odkaz()
            raise
        try:
            self.handle = (self.codex.login_chatgpt_device_code() if druh == "kod"
                           else self.codex.login_chatgpt())
        except Exception:
            self.codex.close()
            _vrat_odkaz()
            raise
        threading.Thread(target=self._cekej, daemon=True).start()

    def _cekej(self) -> None:
        try:
            vysledek = self.handle.wait()
            self.uspech = bool(vysledek.success)
            self.chyba = vysledek.error
        except Exception as chyba:  # noqa: BLE001 — zrušené nebo spadlé přihlášení
            self.chyba = f"{type(chyba).__name__}: {chyba}"
        finally:
            self._hotovo.set()

    def pockej(self, sekund: float) -> bool:
        return self._hotovo.wait(sekund)

    def zrus(self) -> None:
        """Zruší nedokončený pokus, zavře app-server a uklidí odkaz na přihlášení CLI:
        po úspěchu už není potřeba, po neúspěchu se vrátí."""
        if self._uklizeno:
            return
        self._uklizeno = True
        if not self._hotovo.is_set():
            try:
                self.handle.cancel()
            except Exception:  # noqa: BLE001 — app-server už mohl skončit
                pass
            self._hotovo.wait(5)
        self.codex.close()
        if self.uspech:
            ODLOZENY_ODKAZ.unlink(missing_ok=True)
        else:
            _vrat_odkaz()


def zacni(druh: str) -> Prihlaseni:
    """Nový pokus o přihlášení (`druh` = "kod" nebo "prohlizec"); předchozí rozpracovaný zruší."""
    with _start:
        with _zamek:
            predchozi, _rozpracovane["pokus"] = _rozpracovane["pokus"], None
        if predchozi is not None:
            predchozi.zrus()
        pokus = Prihlaseni(druh)
        with _zamek:
            _rozpracovane["pokus"] = pokus
        return pokus


def dokonci(pokus: Prihlaseni) -> None:
    """Uklidí dokončený (nebo vypršelý) pokus."""
    pokus.zrus()
    with _zamek:
        if _rozpracovane["pokus"] is pokus:
            _rozpracovane["pokus"] = None


def odhlas() -> str:
    """Odhlásí lab. Sdílené přihlášení Codex CLI jen odpojí, nikdy ho neodhlásí."""
    with _start:
        with _zamek:
            pokus, _rozpracovane["pokus"] = _rozpracovane["pokus"], None
        if pokus is not None:
            pokus.zrus()
    ODLOZENY_ODKAZ.unlink(missing_ok=True)
    if sdileny():
        AUTH.unlink()
        return "Lab odpojen od přihlášení Codex CLI (to samotné zůstalo přihlášené)."
    if not AUTH.exists():
        return "Lab nebyl přihlášený."
    with Codex(config=_konfigurace()) as codex:
        codex.logout()
    return "Odhlášeno."
