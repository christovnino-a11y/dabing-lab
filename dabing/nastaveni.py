"""Uložená volba překladače a modelů (`nastaveni.json` v kořeni labu, v .gitignore)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import lokalni
from .config import LAB

SOUBOR = LAB / "nastaveni.json"
PREKLADACE = ("codex", "ollama", "lmstudio")


def nacti(soubor: Path | None = None) -> dict:
    try:
        data = json.loads((soubor or SOUBOR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def uloz(data: dict, soubor: Path | None = None) -> None:
    cil = soubor or SOUBOR
    docasny = cil.with_name(cil.name + ".tmp")
    docasny.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(docasny, cil)


def ulozeny_prekladac(soubor: Path | None = None) -> str:
    volba = nacti(soubor).get("prekladac")
    return volba if volba in PREKLADACE else ""


def ulozeny_model(sluzba: str, soubor: Path | None = None) -> str:
    modely = nacti(soubor).get("modely")
    model = modely.get(sluzba) if isinstance(modely, dict) else ""
    return model if isinstance(model, str) else ""


def uloz_volbu(prekladac: str | None = None, sluzba: str | None = None, model: str | None = None,
               soubor: Path | None = None) -> None:
    """Zapíše změněnou položku, ostatní nechá."""
    data = nacti(soubor)
    if prekladac in PREKLADACE:
        data["prekladac"] = prekladac
    if sluzba in lokalni.SLUZBY and model:
        modely = data.get("modely") if isinstance(data.get("modely"), dict) else {}
        modely[sluzba] = model
        data["modely"] = modely
    try:
        uloz(data, soubor)
    except OSError:
        pass  # volba se jen nezapamatuje


def ulozena_adresa(sluzba: str, soubor: Path | None = None) -> str:
    adresy = nacti(soubor).get("adresy")
    adresa = adresy.get(sluzba) if isinstance(adresy, dict) else ""
    return adresa.strip() if isinstance(adresa, str) else ""


def uloz_adresu(sluzba: str, adresa: str, soubor: Path | None = None) -> None:
    """Adresa API služby; prázdná = výchozí (uložená se smaže). Bez schématu se doplní http://."""
    if sluzba not in lokalni.SLUZBY:
        return
    adresa = (adresa or "").strip().rstrip("/")
    if adresa and "://" not in adresa:
        adresa = "http://" + adresa
    if adresa == lokalni.VYCHOZI[sluzba]:
        adresa = ""
    if adresa == ulozena_adresa(sluzba, soubor):
        return  # beze změny nic nezapisovat
    data = nacti(soubor)
    adresy = data.get("adresy") if isinstance(data.get("adresy"), dict) else {}
    if adresa:
        adresy[sluzba] = adresa
    else:
        adresy.pop(sluzba, None)
    data["adresy"] = adresy
    try:
        uloz(data, soubor)
    except OSError:
        pass  # adresa se jen nezapamatuje


def vychozi_prekladac() -> str:
    if volba := ulozeny_prekladac():
        return volba
    from . import ucet  # těžší import (Codex SDK) jen když je potřeba
    if ucet.stav().prihlasen:
        return "codex"
    for sluzba in ("ollama", "lmstudio"):
        s = lokalni.stav(sluzba)
        if s.bezi and s.modely:
            return sluzba
    return "codex"
