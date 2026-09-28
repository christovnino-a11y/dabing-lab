"""Klient na `codex app-server` pro Dabing lab.

Izolace (proč tak, viz poznámka dabing-lab ve vaultu):
- vlastní CODEX_HOME (`codex_home/`) — nedědí hooky, skills, MCP ani AGENTS.md,
- prázdný pracovní adresář (`codex_cwd/`) — Codex nemá co číst ani prohledávat,
- z prostředí se odstraní API klíče, jede se jen přes předplatné ChatGPT,
- ephemeral vlákna, read-only sandbox, každá žádost o schválení se odmítne.

Audio posílá nízkoúrovňové `turn_start` jako `{"type": "localAudio", "path": …}`;
vysokoúrovňové API SDK audio neumí (TypeError v `_inputs.py`).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai_codex.client import CodexClient, CodexConfig

LAB = Path(__file__).resolve().parent.parent
CODEX_HOME = LAB / "codex_home"
CODEX_CWD = LAB / "codex_cwd"

VYCHOZI_MODEL = "gpt-5.6-terra"
VYCHOZI_EFFORT = "medium"  # "minimal" vrací prázdnou odpověď bez chyby

# Klíče, kvůli kterým by Codex mohl jet přes placené API místo předplatného.
_KLICE_API = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
)

# Jak odmítnout každou žádost, kterou app-server může poslat klientovi.
_ODMITNUTI: dict[str, Any] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "abort"},
    "applyPatchApproval": {"decision": "abort"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None},
    "item/tool/requestUserInput": {"answers": {}},
}


def audio(path: str | Path) -> dict[str, str]:
    """Vstupní položka turnu se zvukovým souborem (wav/mp3/m4a/webm/ogg)."""
    return {"type": "localAudio", "path": str(Path(path).resolve())}


def text(obsah: str) -> dict[str, str]:
    return {"type": "text", "text": obsah}


def _slovnik(payload: Any) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(by_alias=True, mode="json", exclude_none=True)
    if hasattr(payload, "params"):
        return dict(payload.params or {})
    return {}


@dataclass
class Vysledek:
    text: str
    status: str
    chyba: str | None = None
    tokeny: dict | None = None
    sekundy: float = 0.0
    udalosti: list[str] = field(default_factory=list)
    # Typy dokončených položek turnu (agentMessage, reasoning, commandExecution…).
    # Když se objeví commandExecution, model si pomáhal shellem, ne jen poslechem.
    typy_polozek: list[str] = field(default_factory=list)

    def json(self) -> Any:
        """Odpověď turnu s `outputSchema` je JSON v textu poslední zprávy."""
        return json.loads(self.text)


class CodexChyba(RuntimeError):
    pass


class Codex:
    """Jedno spojení na app-server. Každý `turn()` dostane nové ephemeral vlákno."""

    def __init__(self, model: str = VYCHOZI_MODEL, effort: str = VYCHOZI_EFFORT) -> None:
        self.model = model
        self.effort = effort
        self.klient: CodexClient | None = None

    def __enter__(self) -> "Codex":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        for klic in _KLICE_API:
            os.environ.pop(klic, None)
        CODEX_CWD.mkdir(parents=True, exist_ok=True)
        konfigurace = CodexConfig(
            cwd=str(CODEX_CWD),
            env={"CODEX_HOME": str(CODEX_HOME)},
            experimental_api=True,
            client_name="dabing_lab",
            client_title="Dabing lab",
        )
        self.klient = CodexClient(konfigurace, approval_handler=self._odmitni)
        self.klient.start()
        self.klient.initialize()
        self._over_predplatne()

    def close(self) -> None:
        if self.klient is not None:
            self.klient.close()
            self.klient = None

    @staticmethod
    def _odmitni(metoda: str, _params: Any) -> dict:
        return _ODMITNUTI.get(metoda, {})

    def _surove(self, metoda: str, params: dict | None = None) -> Any:
        assert self.klient is not None, "Codex není spuštěný"
        return self.klient._request_raw(metoda, params)

    def _over_predplatne(self) -> None:
        ucet = self._surove("account/read", {}) or {}
        druh = json.dumps(ucet).lower()
        if '"apikey"' in druh and '"chatgpt"' not in druh:
            raise CodexChyba("Codex je přihlášený API klíčem, lab jede jen přes předplatné.")

    def limity(self) -> dict:
        """Stav kvóty (`account/rateLimits/read`) — pro výpis před a po běhu."""
        return self._surove("account/rateLimits/read", None) or {}

    def modely(self) -> list[str]:
        assert self.klient is not None
        odpoved = self.klient.model_list(include_hidden=True)
        return [m.id for m in odpoved.data] if hasattr(odpoved, "data") else []

    def turn(
        self,
        polozky: list[dict],
        *,
        schema: dict | None = None,
        pokyny: str | None = None,
        zakladni_pokyny: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        timeout: float = 900.0,
    ) -> Vysledek:
        """Jeden turn v novém ephemeral vlákně. Blokuje do `turn/completed`."""
        assert self.klient is not None, "Codex není spuštěný"
        start = time.monotonic()
        vlakno: dict[str, Any] = {
            "ephemeral": True,
            "cwd": str(CODEX_CWD),
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "model": model or self.model,
        }
        if pokyny:
            vlakno["developerInstructions"] = pokyny
        if zakladni_pokyny:
            vlakno["baseInstructions"] = zakladni_pokyny
        thread_id = self.klient.thread_start(vlakno).thread.id

        parametry: dict[str, Any] = {"effort": effort or self.effort}
        if schema is not None:
            parametry["outputSchema"] = schema
        turn_id = self.klient.turn_start(thread_id, polozky, params=parametry).turn.id

        hlidac = threading.Timer(timeout, self._prerus, args=(thread_id, turn_id))
        hlidac.daemon = True
        hlidac.start()

        zpravy: list[str] = []
        typy: list[str] = []
        chyby: list[str] = []
        udalosti: list[str] = []
        tokeny: dict | None = None
        status = "inProgress"
        self.klient.register_turn_notifications(turn_id)
        try:
            while True:
                oznameni = self.klient.next_turn_notification(turn_id)
                udalosti.append(oznameni.method)
                data = _slovnik(oznameni.payload)
                if oznameni.method == "item/completed":
                    polozka = data.get("item", {})
                    typy.append(str(polozka.get("type")))
                    if polozka.get("type") == "agentMessage" and polozka.get("text"):
                        zpravy.append(polozka["text"])
                elif oznameni.method == "thread/tokenUsage/updated":
                    tokeny = data.get("tokenUsage")
                elif oznameni.method == "error":
                    chyba = data.get("error") or {}
                    chyby.append(chyba.get("message") or json.dumps(chyba, ensure_ascii=False))
                elif oznameni.method == "turn/completed":
                    turn = data.get("turn", {})
                    if turn.get("id") != turn_id:
                        continue
                    status = turn.get("status", "completed")
                    if turn.get("error"):
                        chyby.append(turn["error"].get("message") or "chyba turnu")
                    break
        finally:
            hlidac.cancel()
            self.klient.unregister_turn_notifications(turn_id)

        return Vysledek(
            text=zpravy[-1] if zpravy else "",
            status=status,
            chyba="; ".join(chyby) or None,
            tokeny=tokeny,
            sekundy=time.monotonic() - start,
            udalosti=udalosti,
            typy_polozek=typy,
        )

    def _prerus(self, thread_id: str, turn_id: str) -> None:
        try:
            assert self.klient is not None
            self.klient.turn_interrupt(thread_id, turn_id)
        except Exception:  # noqa: BLE001 — hlídač nesmí shodit volajícího
            pass

    def stderr(self) -> list[str]:
        """Posledních ~400 řádků stderr app-serveru (pro diagnostiku chyb)."""
        if self.klient is None:
            return []
        return list(getattr(self.klient, "_stderr_lines", []))
