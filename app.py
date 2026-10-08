"""Dabing lab — YouTube video do češtiny (verze A).

Přepis Parakeet, mluvčí Nemotron, překlad Codex (nebo offline LM Studio),
hlas OmniVoice klonem mluvčího s emocí z originálu. Gradio na :7872.

Tento proces nenačítá torch ani mlx: těžké kroky běží jako podprocesy
a OmniVoice ve vlastním workeru (viz dabing/pipeline.py).
"""

from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path

import gradio as gr

from dabing import ucet
from dabing.config import JAZYKY, JAZYKY_ZDROJE, PORT
from dabing.job import Zakazka
from dabing.pipeline import Pipeline
from dabing.tts import Zruseno

REZIMY = {"S Codexem (nejlepší překlad)": "codex", "Plně lokální (nic neodchází z Macu)": "offline"}
MLUVCI = ["Automaticky", "1", "2", "3", "4", "5", "6"]


def _pohlavi(text: str) -> dict[str, str]:
    """„1 ž, 2 m" → {"0": "z", "1": "m"} (číslo mluvčího jako v tabulce, M1 = 1). Prázdné = z hlasu."""
    vysledek = {}
    for cislo, rod in re.findall(r"(?:^|[\s,;])M?(\d+)\s*[:=-]?\s*(ž|z|f|w|m|muž|žena)", text or "", re.I):
        vysledek[str(int(cislo) - 1)] = "z" if rod.lower()[0] in "žzfw" else "m"
    return vysledek


QA_POPISY = {"zrychleno": "mírně zrychleno", "zpomaleno": "mírně zpomaleno", "presah": "PŘESAH — zkrať text",
             "identita": "hlas opraven novým pokusem",
             "hlas_ujel": "HLAS NESEDÍ — poslechni",
             "nesrozumitelne": "ŠPATNĚ SROZUMITELNÉ — poslechni",
             "bez_dabingu": "bez překladu — zní originál"}
SLOUPCE = ["#", "Čas", "Mluvčí", "Originál", "Překlad (lze upravit)", "Délka hlasu", "Kontrola"]

_beh: dict[str, Pipeline | None] = {"pipeline": None}
_zamek = threading.Lock()


def _cas(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


def _tabulka(z: Zakazka) -> list[list]:
    radky = []
    for v in z.vety:
        kontrola = [QA_POPISY.get(q, q) for q in v.qa]
        if v.zkraceno:
            kontrola.insert(0, "zkráceno")
        delka = f"{v.vysledna_s:.1f} / {v.delka:.1f} s" if v.vysledna_s else ""
        radky.append([v.id, f"{_cas(v.start)}–{_cas(v.end)}", f"M{v.mluvci + 1}", v.en_preklad, v.cs,
                      delka, ", ".join(kontrola)])
    return radky


def _souhrn(z: Zakazka) -> str:
    vety = z.vety
    zrychleno = sum("zrychleno" in v.qa for v in vety)
    presah = sum("presah" in v.qa for v in vety)
    identita = sum("identita" in v.qa for v in vety)
    nesrozumitelne = sum("nesrozumitelne" in v.qa for v in vety)
    radky = [f"**{z.nazev}** — {len(vety)} vět, mluvčích {len({v.mluvci for v in vety})}.",
             f"Mírně zrychleno {zrychleno}, přesah {presah}, hlas opraven {identita}, "
             f"špatně srozumitelné {nesrozumitelne}."]
    if presah:
        radky.append("Věty s **přesahem** v tabulce zkrať a dej *Přegenerovat změněné*.")
    mixinfo = z.codex.get("mix") or {}
    if mixinfo.get("mix_lufs") is not None and mixinfo.get("original_lufs") is not None:
        radky.append(f"Hlasitost: originál {mixinfo['original_lufs']:.1f} LUFS, dabing "
                     f"{mixinfo['mix_lufs']:.1f} LUFS. Kde mluvčí nemluví, hraje originál beze "
                     "změny; podklad bez jeho hlasu je ke stažení (*.podklad.flac).")
    if z.prekladac == "codex" and z.codex.get("turny"):
        radky.append(f"Codex: {z.codex.get('turny')} turnů, kvóta {z.codex.get('kvota_pred_%')} % → "
                     f"{z.codex.get('kvota_po_%')} %.")
    radky.append("Dabing hlasem OmniVoice (váhy CC-BY-NC) — jen pro nekomerční použití.")
    return "  \n".join(radky)


def _spust_ve_vlakne(prace, udalosti: queue.Queue):
    vysledek: dict = {}

    def cil():
        try:
            vysledek["hodnota"] = prace()
        except Zruseno:
            vysledek["zruseno"] = True
        except Exception as chyba:  # noqa: BLE001 — chyba se ukáže v UI
            vysledek["chyba"] = f"{type(chyba).__name__}: {chyba}"
        finally:
            udalosti.put(None)

    vlakno = threading.Thread(target=cil, daemon=True)
    vlakno.start()
    return vlakno, vysledek


def _prubeh_behu(prace_s_pipeline):
    """Společný generátor: spustí práci na pozadí a průběžně posílá log a stav do UI."""
    udalosti: queue.Queue = queue.Queue()
    log: list[str] = []
    pipeline = Pipeline(lambda r: udalosti.put(("log", r)),
                        lambda p, zprava: udalosti.put(("prubeh", (p, zprava))))
    with _zamek:
        if _beh["pipeline"] is not None:
            yield "Už běží jiný dabing — počkej, nebo ho zruš.", "", gr.skip(), gr.skip(), gr.skip(), gr.skip()
            return
        _beh["pipeline"] = pipeline
    stav = "Začínám…"
    try:
        _, vysledek = _spust_ve_vlakne(lambda: prace_s_pipeline(pipeline), udalosti)
        posledni = 0.0
        while True:
            try:
                udalost = udalosti.get(timeout=0.5)
            except queue.Empty:
                udalost = ("tik", None)
            if udalost is None:
                break
            druh, data = udalost
            if druh == "log":
                log.append(data)
            elif druh == "prubeh":
                p, zprava = data
                stav = f"**{p * 100:.0f} %** — {zprava}"
            if time.monotonic() - posledni > 0.4:
                posledni = time.monotonic()
                yield "\n".join(log[-400:]), stav, gr.skip(), gr.skip(), gr.skip(), gr.skip()
        if vysledek.get("zruseno"):
            yield "\n".join(log[-400:]), "Zrušeno.", gr.skip(), gr.skip(), gr.skip(), gr.skip()
            return
        if "chyba" in vysledek:
            yield ("\n".join(log[-400:]), f"**Chyba:** {vysledek['chyba']}",
                   gr.skip(), gr.skip(), gr.skip(), gr.skip())
            return
        z, soubory = vysledek["hodnota"]
        video = str(soubory["mp4"]) if "mp4" in soubory else None
        yield ("\n".join(log[-400:]), _souhrn(z), video, [str(p) for p in soubory.values()],
               _tabulka(z), z.id)
    finally:
        with _zamek:
            _beh["pipeline"] = None


def spustit(odkaz: str, soubor, rezim: str, jazyk_videa: str, mluvci: str, jazyk: str,
            pohlavi: str = ""):
    zdroj = (soubor if isinstance(soubor, str) else getattr(soubor, "name", None)) or (odkaz or "").strip()
    if not zdroj:
        yield "", "Vlož odkaz na YouTube nebo nahraj soubor.", gr.skip(), gr.skip(), gr.skip(), gr.skip()
        return
    if REZIMY[rezim] == "codex":
        # radši hned než po stažení, separaci a přepisu (desítky minut)
        stav_uctu = ucet.stav()
        if not stav_uctu.prihlasen:
            zprava = ("Překlad Codexem potřebuje přihlášení — přihlas se nahoře v sekci **Codex účet**, "
                      "nebo zvol plně lokální režim." if stav_uctu.overeno else stav_uctu.popis)
            yield "", zprava, gr.skip(), gr.skip(), gr.skip(), gr.skip()
            return
    pocet = None if mluvci == "Automaticky" else int(mluvci)
    yield from _prubeh_behu(lambda p: p.cely(zdroj, JAZYKY[jazyk], REZIMY[rezim], pocet,
                                             JAZYKY_ZDROJE[jazyk_videa], _pohlavi(pohlavi)))


def pregenerovat(id_zakazky: str, tabulka):
    if not id_zakazky:
        yield "", "Nejdřív dokonči dabing.", gr.skip(), gr.skip(), gr.skip(), gr.skip()
        return
    radky = tabulka.values.tolist() if hasattr(tabulka, "values") else list(tabulka or [])
    zmeny = {int(r[0]): str(r[4] or "").strip() for r in radky if str(r[4] or "").strip()}
    if not any(v.cs != zmeny[v.id] for v in Zakazka.nacti(id_zakazky).vety if v.id in zmeny):
        yield "", "V tabulce se nic nezměnilo.", gr.skip(), gr.skip(), gr.skip(), gr.skip()
        return

    def prace(p: Pipeline):
        # úpravy se zapíší až po rezervaci běhu, ať je nepřepíše jiný běžící dabing
        z = Zakazka.nacti(id_zakazky)
        for v in z.vety:
            novy = zmeny.get(v.id)
            if novy and novy != v.cs:
                # ručně upravený text jde do hlasu tak, jak je napsaný (i s fonetikou)
                v.cs, v.vyslov, v.upraveno, v.zkraceno = novy, novy, True, False
        z.uloz()
        return z, p.nadabuj(z)
    yield from _prubeh_behu(prace)


def zrusit():
    pipeline = _beh["pipeline"]
    if pipeline is None:
        return "Nic neběží."
    pipeline.zrus()
    return "Zastavuji…"


# ---------------------------------------------------------------- účet Codexu
def ukaz_ucet():
    stav = ucet.stav()
    return stav.popis, gr.update(open=not stav.prihlasen)


def _prihlasit(druh: str):
    """Spustí přihlášení účtem ChatGPT a do UI průběžně píše návod a stav, dokud neskončí."""
    with _zamek:
        bezi = _beh["pipeline"] is not None
    if bezi:
        yield "Právě běží dabing — přihlášení změň, až doběhne."
        return
    try:
        pokus = ucet.zacni(druh)
    except Exception as chyba:  # noqa: BLE001 — chyba se ukáže v UI
        yield f"**Přihlášení se nepodařilo spustit:** {type(chyba).__name__}: {chyba}"
        return
    if druh == "kod":
        url = pokus.handle.verification_url
        navod = (f"1. Otevři **[{url}]({url})** — klidně na telefonu nebo jiném počítači.  \n"
                 f"2. Přihlas se účtem ChatGPT a zadej kód **`{pokus.handle.user_code}`**.")
    else:
        navod = (f"**[Otevři přihlášení OpenAI]({pokus.handle.auth_url})** v prohlížeči na tomto "
                 "počítači a přihlas se účtem ChatGPT. Stránka OpenAI se pak vrátí na localhost.")
    start = time.monotonic()
    try:
        while not pokus.pockej(2.0):
            zbyva = ucet.CEKANI_S - (time.monotonic() - start)
            if zbyva <= 0:
                yield "Přihlášení vypršelo — zkus to znovu."
                return
            yield f"{navod}\n\nČekám na dokončení přihlášení (ještě ~{zbyva / 60:.0f} min)…"
        if pokus.uspech:
            yield ucet.stav().popis
        else:
            yield f"Přihlášení se nepodařilo: {pokus.chyba or 'zrušeno'}.  \n{ucet.stav().popis}"
    finally:
        ucet.dokonci(pokus)  # zavře app-server i při zavření stránky nebo vypršení


def prihlasit_kodem():
    yield from _prihlasit("kod")


def prihlasit_prohlizecem():
    yield from _prihlasit("prohlizec")


def odhlasit():
    with _zamek:
        bezi = _beh["pipeline"] is not None
    if bezi:
        return "Právě běží dabing — odhlas se, až doběhne."
    try:
        zprava = ucet.odhlas()
    except Exception as chyba:  # noqa: BLE001
        zprava = f"Odhlášení se nepodařilo: {type(chyba).__name__}: {chyba}"
    return f"{zprava}  \n{ucet.stav().popis}"


with gr.Blocks(title="Dabing") as demo:
    gr.Markdown("# Dabing\nVideo z YouTube nebo souboru do češtiny či jiného jazyka: přepis, mluvčí, "
                "překlad a hlas naklonovaný z originálu, ostatní zvuky zůstávají.")
    with gr.Accordion("Codex účet (překlad)", open=False) as ucet_panel:
        gr.Markdown("Překlad Codexem jede přes tvé předplatné ChatGPT (ne přes placené API). "
                    "Přihlášení zůstane uložené v `codex_home/` tohoto labu, do gitu nejde.")
        ucet_stav = gr.Markdown("Zjišťuji stav účtu…")
        with gr.Row():
            prihlas_kod = gr.Button("Přihlásit kódem", variant="primary")
            prihlas_web = gr.Button("Přihlásit v prohlížeči")
            odhlas = gr.Button("Odhlásit")
    with gr.Row():
        odkaz = gr.Textbox(label="Odkaz na YouTube", placeholder="https://youtu.be/…", scale=3)
        soubor = gr.File(label="…nebo vlastní video/zvuk", file_types=["video", "audio"],
                         type="filepath", scale=2)
    rezim = gr.Radio(label="Režim", choices=list(REZIMY), value=next(iter(REZIMY)),
                     info="Přepis, mluvčí a klon hlasu běží vždy lokálně. Režim určuje, kdo "
                          "překládá: Codex, nebo model v LM Studiu (musí v něm běžet server).")
    with gr.Row():
        jazyk_videa = gr.Dropdown(label="Jazyk videa", choices=list(JAZYKY_ZDROJE),
                                  value="Automaticky", scale=1)
        mluvci = gr.Dropdown(label="Počet mluvčích", choices=MLUVCI, value="Automaticky", scale=1)
        jazyk = gr.Dropdown(label="Jazyk dabingu", choices=list(JAZYKY), value="Čeština", scale=1)
        pohlavi = gr.Textbox(label="Pohlaví mluvčích (volitelně)", placeholder="např. 1 ž, 2 m", scale=1,
                             info="Pohlaví se pozná z hlasu; sem napiš jen když to nesedí. "
                                  "Číslo mluvčího jako v tabulce (M1 = 1). Rozhoduje o rodu sloves v překladu.")
    with gr.Row():
        spust = gr.Button("Spustit", variant="primary", scale=3)
        zrus = gr.Button("Zrušit", variant="stop", scale=1)
    stav = gr.Markdown()
    with gr.Row():
        video = gr.Video(label="Výsledek", scale=3)
        soubory = gr.File(label="Stáhnout", file_count="multiple", scale=2)
    tabulka = gr.Dataframe(label="Věty — překlad můžeš upravit", headers=SLOUPCE, wrap=True,
                           interactive=True, static_columns=[0, 1, 2, 3, 5, 6],
                           column_widths=["4%", "10%", "6%", "32%", "32%", "8%", "8%"])
    pregen = gr.Button("Přegenerovat změněné")
    with gr.Accordion("Log", open=False):
        log = gr.Textbox(show_label=False, lines=14, max_lines=14, autoscroll=True)
    id_zakazky = gr.State("")

    vystupy = [log, stav, video, soubory, tabulka, id_zakazky]
    spust.click(spustit, [odkaz, soubor, rezim, jazyk_videa, mluvci, jazyk, pohlavi], vystupy)
    pregen.click(pregenerovat, [id_zakazky, tabulka], vystupy)
    zrus.click(zrusit, None, stav)
    prihlas_kod.click(prihlasit_kodem, None, ucet_stav)
    prihlas_web.click(prihlasit_prohlizecem, None, ucet_stav)
    odhlas.click(odhlasit, None, ucet_stav)
    demo.load(ukaz_ucet, None, [ucet_stav, ucet_panel])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="127.0.0.1", server_port=PORT, inbrowser=False, show_error=True,
        allowed_paths=[str(Path(__file__).resolve().parent / "jobs")],
    )
