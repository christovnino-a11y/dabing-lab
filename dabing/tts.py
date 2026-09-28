"""Hlas verze A: OmniVoice klon ve workeru, hlídání identity a délky.

Postup (kvalita před rychlostí):
1. Každá věta se vygeneruje přirozeně s referencí „věta" (emoce z originálu)
   nebo „mluvčí".
2. Hlídač identity: podobnost ECAPA výstupu se stálou referencí mluvčího. Věty,
   které z řady mluvčího vybočují, se přegenerují se stálou referencí.
3. Délka: co se vejde do slotu (věta + půjčka z mezery za ní), zůstane. Do 1,15×
   dostane OmniVoice `duration` = slot. Delší věty Codex dávkově zkrátí a pak
   se znovu měří. Nikdy rychleji než 1/0,85 přirozené řeči, zbytek dostane QA přesah.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from . import refs, translate
from .config import (CIL_VYPLNENI, KOTVA_MIN_PODOBNOST, KOTVA_SEEDY, KOTVA_TEXT, MAX_POZICKA_S, MAX_ZPOMALENI, MIN_RYCHLOST,
                     PRAH_KRATKE_VETY, REFERENCE_HLASU, REZERVA_ZA_VETOU_S, WORKER_PYTHON,
                     WORKER_SKRIPT, ZRYCHLENI_BEZ_OBAV, offline_env)
from .job import Veta, Zakazka, otisk_tts

Log = Callable[[str], None]
Prubeh = Callable[[float, str], None]

KROKY = 32
IDENTITA_ODCHYLKA = 0.12
IDENTITA_MINIMUM = 0.35
TIMEOUT_VETY_S = 300.0
PRAH_PRODLOUZENI = 0.75   # hlas kratší než 75 % originálu → nechat text přeformulovat plněji
MIN_PRODLOUZENI_S = 2.0   # u kratších vět se ticho neřeší (stačí zpomalení)


class WorkerSpadl(RuntimeError):
    pass


class Zruseno(RuntimeError):
    pass


class Worker:
    """Proces s OmniVoice. Ve stejné skupině procesů jako lab (portál ho tak ukončí)."""

    def __init__(self, log: Log, log_soubor: Path):
        self.log = log
        self.log_soubor = log_soubor
        self.proces: subprocess.Popen | None = None
        self.fronta: queue.Queue = queue.Queue()

    def start(self) -> None:
        self.log_soubor.parent.mkdir(parents=True, exist_ok=True)
        chyby = open(self.log_soubor, "a", encoding="utf-8")
        self.proces = subprocess.Popen(
            [str(WORKER_PYTHON), "-W", "ignore", str(WORKER_SKRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=chyby,
            text=True, bufsize=1, env=offline_env(), cwd=str(WORKER_SKRIPT.parent),
        )
        self.fronta = queue.Queue()
        threading.Thread(target=self._cti, args=(self.proces, self.fronta), daemon=True).start()
        uvod = self._odpoved(60.0)
        if not uvod.get("pripraven"):
            raise WorkerSpadl(f"Worker nenaběhl: {uvod}")
        self.log(f"OmniVoice worker běží ({uvod.get('zarizeni')}), načítám model…")
        nacteni = self.dotaz({"op": "nacti"}, timeout=600.0)
        if not nacteni.get("ok"):
            raise WorkerSpadl(f"OmniVoice se nenačetl: {nacteni.get('chyba')}")

    @staticmethod
    def _cti(proces: subprocess.Popen, fronta: queue.Queue) -> None:
        assert proces.stdout is not None
        for radek in proces.stdout:
            radek = radek.strip()
            if radek:
                try:
                    fronta.put(json.loads(radek))
                except json.JSONDecodeError:
                    pass
        fronta.put(None)  # proces skončil

    def _odpoved(self, timeout: float) -> dict:
        try:
            odpoved = self.fronta.get(timeout=timeout)
        except queue.Empty:
            self.zabij()
            raise WorkerSpadl(f"Worker neodpověděl do {timeout:.0f} s")
        if odpoved is None:
            raise WorkerSpadl("Worker skončil (nejspíš chyba MPS) — podrobnosti v logy/omnivoice.log")
        return odpoved

    def dotaz(self, zadani: dict, timeout: float = TIMEOUT_VETY_S) -> dict:
        if self.proces is None or self.proces.poll() is not None:
            raise WorkerSpadl("Worker neběží")
        assert self.proces.stdin is not None
        self.proces.stdin.write(json.dumps(zadani, ensure_ascii=False) + "\n")
        self.proces.stdin.flush()
        return self._odpoved(timeout)

    def zabij(self) -> None:
        if self.proces is not None and self.proces.poll() is None:
            self.proces.kill()
            self.proces.wait(timeout=10)

    def zavri(self) -> None:
        if self.proces is None:
            return
        if self.proces.poll() is None:
            try:
                self.dotaz({"op": "konec"}, timeout=10.0)
            except WorkerSpadl:
                pass
            try:
                self.proces.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.zabij()
        self.proces = None


def _orizni_ticho(cesta: Path) -> float:
    """Ořízne ticho na začátku a konci (−45 dB pod špičkou, 20 ms rezerva). Vrací délku v s."""
    x, sr = sf.read(cesta, dtype="float32")
    if x.size == 0:
        return 0.0
    ramec = int(0.01 * sr)
    n = len(x) // ramec
    if n < 3:
        return len(x) / sr
    energie = np.sqrt((x[: n * ramec].reshape(n, ramec) ** 2).mean(axis=1) + 1e-12)
    prah = energie.max() * 10 ** (-45 / 20)
    hlas = np.flatnonzero(energie > prah)
    if hlas.size == 0:
        return len(x) / sr
    a = max(0, (hlas[0] - 2) * ramec)
    b = min(len(x), (hlas[-1] + 3) * ramec)
    if a > 0 or b < len(x):
        sf.write(cesta, x[a:b], sr, subtype="FLOAT")
    return (b - a) / sr


def slot(zakazka: Zakazka, i: int) -> float:
    v = zakazka.vety[i]
    konec_videa = zakazka.delka_s or v.end + MAX_POZICKA_S
    dalsi = zakazka.vety[i + 1].start if i + 1 < len(zakazka.vety) else min(v.end + MAX_POZICKA_S, konec_videa)
    return v.delka + max(0.0, min(dalsi - v.end - REZERVA_ZA_VETOU_S, MAX_POZICKA_S))


class Hlas:
    def __init__(self, zakazka: Zakazka, log: Log, zrusit: threading.Event):
        self.z = zakazka
        self.log = log
        self.zrusit = zrusit
        self.worker = Worker(log, zakazka.slozka.parent.parent / "logy" / "omnivoice.log")
        self.mluvci_ref: dict[int, str] = {}
        self.kotvy: dict[int, str] = {}

    def _synth(self, v: Veta, vynucena: float | None) -> float:
        """Vygeneruje větu (s cache), vrátí délku po oříznutí ticha."""
        if self.zrusit.is_set():
            raise Zruseno()
        v.vynucena_s = vynucena
        reference = self.z.slozka / v.reference
        otisk = otisk_tts(v, self.z.jazyk, KROKY, reference.stat().st_mtime)
        cil = self.z.cesta(f"tts/veta_{v.id:04d}.wav")
        if v.tts_otisk == otisk and cil.exists():
            return v.vysledna_s if vynucena else v.prirozena_s
        cil.parent.mkdir(parents=True, exist_ok=True)
        zadani = {"op": "synth", "id": v.id, "text": v.vyslov or v.cs, "jazyk": self.z.jazyk,
                  "ref_audio": str(reference), "ref_text": refs.text_reference(self.z, v.reference),
                  "kroky": KROKY, "seed": v.seed, "vystup": str(cil)}
        if vynucena:
            zadani["duration"] = round(vynucena, 3)
        for pokus in range(2):
            try:
                odpoved = self.worker.dotaz(zadani)
            except WorkerSpadl as chyba:
                self.log(f"Věta {v.id}: {chyba} — restartuji worker")
                self.worker.zabij()
                self.worker.start()
                zadani["kroky"] = 16
                continue
            if odpoved.get("ok"):
                break
            self.log(f"Věta {v.id}: {odpoved.get('chyba')}")
            if pokus == 1:
                raise RuntimeError(f"Věta {v.id} se nepodařila: {odpoved.get('chyba')}")
        else:
            raise RuntimeError(f"Věta {v.id}: worker opakovaně spadl")
        delka = _orizni_ticho(cil)
        v.tts = str(cil.relative_to(self.z.slozka))
        v.tts_otisk = otisk
        if vynucena:
            v.vysledna_s = delka
        else:
            v.prirozena_s = v.vysledna_s = delka
        return delka

    def _kotva(self, m: int, stala: str) -> str | None:
        """Věta v cílovém jazyce namluvená klonem mluvčího (z jeho anglické reference), zamčená
        jako reference pro všechny jeho věty: hlas zůstane jeho, ale výslovnost je česká —
        z anglické reference by se přenášel anglický přízvuk. Ze 3 pokusů vyhraje zdravý
        take nejpodobnější jeho hlasu. Hotová kotva se znovu nepočítá."""
        text = KOTVA_TEXT[self.z.jazyk]
        zdroj = self.z.slozka / stala
        cil = self.z.cesta(f"refs/kotva_{self.z.jazyk}_{m}.wav")
        popis, mereni = cil.with_suffix(".txt"), cil.with_suffix(".json")
        if (cil.exists() and popis.exists() and popis.read_text(encoding="utf-8") == text
                and cil.stat().st_mtime >= zdroj.stat().st_mtime):
            podobnost = json.loads(mereni.read_text()).get("podobnost", 1.0) if mereni.exists() else 1.0
            return str(cil.relative_to(self.z.slozka)) if podobnost >= KOTVA_MIN_PODOBNOST else None
        kandidati: list[tuple[bool, float, Path]] = []
        for seed in KOTVA_SEEDY:
            if self.zrusit.is_set():
                raise Zruseno()
            pokus = cil.with_name(f"{cil.stem}_s{seed}.wav")
            try:
                odpoved = self.worker.dotaz({
                    "op": "synth", "id": f"kotva_{m}", "text": text, "jazyk": self.z.jazyk,
                    "ref_audio": str(zdroj), "ref_text": refs.text_reference(self.z, stala),
                    "kroky": KROKY, "seed": seed, "vystup": str(pokus)})
                if not odpoved.get("ok"):
                    self.log(f"Kotva M{m + 1} (pokus {seed}): {odpoved.get('chyba')}")
                    continue
                delka = _orizni_ticho(pokus)
                podobnost = self.worker.dotaz({"op": "identita", "a": str(pokus), "b": str(zdroj)},
                                              timeout=120.0).get("podobnost") or 0.0
            except WorkerSpadl as chyba:
                self.log(f"Kotva M{m + 1} (pokus {seed}): {chyba} — restartuji worker")
                self.worker.zabij()
                self.worker.start()
                continue
            zdravy = bool(len(text) / 22 < delka < len(text) / 8)  # ne useknutá ani natažená
            kandidati.append((zdravy, float(podobnost), pokus))
        if not kandidati:
            self.log(f"Mluvčí M{m + 1}: kotvu se nepodařilo namluvit — klonuji z původních vět.")
            return None
        zdravy, podobnost, vitez = max(kandidati, key=lambda k: (k[0], k[1]))
        if podobnost < KOTVA_MIN_PODOBNOST:
            for _, _, pokus in kandidati:
                pokus.unlink(missing_ok=True)
            cil.unlink(missing_ok=True)
            self.log(f"Mluvčí M{m + 1}: kotva hlasu mu není dost podobná ({podobnost:.2f}, "
                     "nejspíš málo jeho řeči) — klonuji přímo z jeho vět.")
            return None
        os.replace(vitez, cil)
        popis.write_text(text, encoding="utf-8")
        mereni.write_text(json.dumps({"podobnost": round(float(podobnost), 3), "zdravy": bool(zdravy)}))
        for _, _, pokus in kandidati:
            pokus.unlink(missing_ok=True)
        self.log(f"Mluvčí M{m + 1}: kotva hlasu ({self.z.jazyk}) hotová (podobnost {podobnost:.2f}"
                 f"{'' if zdravy else ', žádný pokus nebyl úplně v pořádku'})")
        return str(cil.relative_to(self.z.slozka))

    def _identita(self, prubeh: Prubeh) -> None:
        """Věty, které hlasem vybočují z řady mluvčího, přegenerovat (kotvu jiným pokusem,
        emoční referenci kotvou nebo stálou referencí)."""
        for m, stala in self.mluvci_ref.items():
            kotva = self.kotvy.get(m)
            vety = [v for v in self.z.vety
                    if v.mluvci == m and v.druh_reference in ("veta", "kotva") and v.tts]
            if len(vety) < 3:
                continue
            for v in vety:
                srovnat = kotva if v.druh_reference == "kotva" and kotva else stala
                odpoved = self.worker.dotaz({"op": "identita", "a": str(self.z.slozka / v.tts),
                                             "b": str(self.z.slozka / srovnat)}, timeout=120.0)
                v.identita = odpoved.get("podobnost") if odpoved.get("ok") else None
            hodnoty = [v.identita for v in vety if v.identita is not None]
            if not hodnoty:
                continue
            median = float(np.median(hodnoty))
            ulet = [v for v in vety if v.identita is not None
                    and (v.identita < median - IDENTITA_ODCHYLKA or v.identita < IDENTITA_MINIMUM)]
            for v in ulet:
                if v.druh_reference == "kotva":
                    self.log(f"Věta {v.id}: hlas vybočil (podobnost {v.identita:.2f} vs medián "
                             f"{median:.2f}) — generuji znovu jiným pokusem")
                    v.seed += 1
                else:
                    self.log(f"Věta {v.id}: hlas vybočil (podobnost {v.identita:.2f} vs medián "
                             f"{median:.2f}) — generuji se stálou referencí mluvčího")
                    v.reference, v.druh_reference = (kotva, "kotva") if kotva else (stala, "mluvci")
                v.qa = sorted(set(v.qa) | {"identita"})
                self._synth(v, None)
            prubeh(0.8, f"Hlídač identity: mluvčí M{m + 1}, přegenerováno {len(ulet)}")
        self.z.uloz()

    def _kotvy(self, prubeh: Prubeh) -> None:
        """Kotvy všech mluvčích (hotové se jen načtou) a z nich změřená rychlost řeči hlasu."""
        if REFERENCE_HLASU != "kotva" or self.z.jazyk not in KOTVA_TEXT:
            return
        prubeh(0.0, "Namlouvám kotvu hlasu v cílovém jazyce…")
        text = KOTVA_TEXT[self.z.jazyk]
        for m, stala in self.mluvci_ref.items():
            kotva = self._kotva(m, stala)
            if kotva:
                self.kotvy[m] = kotva
                delka = sf.info(str(self.z.slozka / kotva)).duration
                self.z.rychlost_hlasu[str(m)] = round(len(text) / max(delka, 0.5), 2)
        for v in self.z.vety:
            if v.mluvci in self.kotvy:
                v.reference, v.druh_reference = self.kotvy[v.mluvci], "kotva"
        if self.z.rychlost_hlasu:
            self.log("Rychlost hlasu (změřeno z kotvy): " + ", ".join(
                f"M{int(m) + 1} {r:.1f} znaku/s" for m, r in sorted(self.z.rychlost_hlasu.items())))
        self.z.uloz()

    def priprav_kotvy(self, prubeh: Prubeh) -> None:
        """Před překladem: kotvy a rychlost hlasu, ať překladatel ví, kolik textu se vejde."""
        self.mluvci_ref = refs.priprav(self.z)
        if REFERENCE_HLASU != "kotva" or self.z.jazyk not in KOTVA_TEXT:
            return
        self.worker.start()
        try:
            self._kotvy(prubeh)
        finally:
            self.worker.zavri()

    def nadabuj(self, prubeh: Prubeh) -> None:
        self.mluvci_ref = refs.priprav(self.z)
        vety = [v for v in self.z.vety if (v.vyslov or v.cs).strip()]
        self.worker.start()
        try:
            self._kotvy(prubeh)
            start = time.monotonic()
            for i, v in enumerate(vety):
                self._synth(v, None)
                if i % 5 == 4 or i == len(vety) - 1:
                    ubehlo = time.monotonic() - start
                    zbyva = ubehlo / (i + 1) * (len(vety) - i - 1)
                    prubeh(0.7 * (i + 1) / len(vety),
                           f"Hlas {i + 1}/{len(vety)} (zbývá ~{zbyva / 60:.0f} min)")
                    self.z.uloz()
            self._identita(prubeh)
            self._delky(prubeh)
        finally:
            self.worker.zavri()
        self.z.hotovo("hlas")

    def _delky(self, prubeh: Prubeh) -> None:
        indexy = {v.id: i for i, v in enumerate(self.z.vety)}
        dlouhe: list[tuple[Veta, float]] = []
        for v in self.z.vety:
            if not v.tts:
                continue
            s = slot(self.z, indexy[v.id])
            v.pomer = round(v.prirozena_s / max(v.delka, 0.01), 3)
            if v.prirozena_s > s * ZRYCHLENI_BEZ_OBAV and not v.upraveno:
                dlouhe.append((v, s))
        if dlouhe:
            prubeh(0.85, f"Zkracuji {len(dlouhe)} vět, které se nevejdou…")
            for v, s in dlouhe:
                v.max_znaku = max(8, int(len(v.vyslov or v.cs) * s / v.prirozena_s * 0.95))
            translate.zkrat(self.z, [v for v, _ in dlouhe], self.log)
            for v, _ in dlouhe:
                self._synth(v, None)
        # a naopak: věta výrazně kratší než originál → po ní by zůstalo ticho, zatímco řečník
        # na obraze mluví. Změřená délka hlasu přesně řekne, kolik textu přidat.
        kratke = [(v, slot(self.z, indexy[v.id])) for v in self.z.vety
                  if v.tts and not v.upraveno and v.delka >= MIN_PRODLOUZENI_S
                  and v.prirozena_s < v.delka * PRAH_PRODLOUZENI]
        if kratke:
            prubeh(0.87, f"Prodlužuji {len(kratke)} vět, po kterých by zůstalo ticho…")
            puvodni = {v.id: v.vyslov or v.cs for v, _ in kratke}
            for v, s in kratke:
                znaku_za_s = len(puvodni[v.id]) / max(v.prirozena_s, 0.3)  # tahle věta, tenhle hlas
                v.cil_znaku = int(v.delka * CIL_VYPLNENI * znaku_za_s)
                v.max_znaku = max(v.cil_znaku, int(s * znaku_za_s * 0.95))
            translate.prodluz(self.z, [v for v, _ in kratke], self.log)
            for v, _ in kratke:
                if (v.vyslov or v.cs) != puvodni[v.id]:
                    self._synth(v, None)
        prubeh(0.92, "Dolaďuji délky vět…")
        for v in self.z.vety:
            if not v.tts:
                continue
            s = slot(self.z, indexy[v.id])
            v.pomer = round(v.prirozena_s / max(v.delka, 0.01), 3)
            v.qa = [q for q in v.qa if q not in ("zrychleno", "presah", "zpomaleno")]
            if v.prirozena_s < v.delka * PRAH_KRATKE_VETY:
                # krátká věta: mírně zpomalit, ať řeč pokryje čas, kdy mluví originál
                cil = min(v.delka * 0.95, v.prirozena_s / MAX_ZPOMALENI)
                if cil > v.prirozena_s * 1.03:
                    self._synth(v, cil)
                    v.qa.append("zpomaleno")
                    continue
            if v.prirozena_s <= s:
                v.vynucena_s = None
                v.vysledna_s = v.prirozena_s
                continue
            cil = s if v.prirozena_s / s <= 1 / MIN_RYCHLOST else v.prirozena_s * MIN_RYCHLOST
            self._synth(v, cil)
            v.qa.append("zrychleno" if cil == s else "presah")
        self.z.uloz()
