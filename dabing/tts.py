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
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from . import refs, translate
from .config import (MAX_POZICKA_S, MAX_ZPOMALENI, MIN_RYCHLOST, PRAH_KRATKE_VETY,
                     REZERVA_ZA_VETOU_S, SR_TTS, WORKER_PYTHON, WORKER_SKRIPT,
                     ZRYCHLENI_BEZ_OBAV, offline_env)
from .job import Veta, Zakazka, otisk_tts

Log = Callable[[str], None]
Prubeh = Callable[[float, str], None]

KROKY = 32
IDENTITA_ODCHYLKA = 0.12
IDENTITA_MINIMUM = 0.35
TIMEOUT_VETY_S = 300.0


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
    dalsi = zakazka.vety[i + 1].start if i + 1 < len(zakazka.vety) else v.end + MAX_POZICKA_S
    return v.delka + max(0.0, min(dalsi - v.end - REZERVA_ZA_VETOU_S, MAX_POZICKA_S))


class Hlas:
    def __init__(self, zakazka: Zakazka, log: Log, zrusit: threading.Event):
        self.z = zakazka
        self.log = log
        self.zrusit = zrusit
        self.worker = Worker(log, zakazka.slozka.parent.parent / "logy" / "omnivoice.log")
        self.mluvci_ref: dict[int, str] = {}

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
                  "kroky": KROKY, "vystup": str(cil)}
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

    def _identita(self, prubeh: Prubeh) -> None:
        """Věty s referencí „věta", které hlasem vybočují z řady mluvčího, přegenerovat."""
        for m, stala in self.mluvci_ref.items():
            vety = [v for v in self.z.vety if v.mluvci == m and v.druh_reference == "veta" and v.tts]
            if len(vety) < 3:
                continue
            for v in vety:
                odpoved = self.worker.dotaz({"op": "identita", "a": str(self.z.slozka / v.tts),
                                             "b": str(self.z.slozka / stala)}, timeout=120.0)
                v.identita = odpoved.get("podobnost") if odpoved.get("ok") else None
            hodnoty = [v.identita for v in vety if v.identita is not None]
            if not hodnoty:
                continue
            median = float(np.median(hodnoty))
            ulet = [v for v in vety if v.identita is not None
                    and (v.identita < median - IDENTITA_ODCHYLKA or v.identita < IDENTITA_MINIMUM)]
            for v in ulet:
                self.log(f"Věta {v.id}: hlas vybočil (podobnost {v.identita:.2f} vs medián "
                         f"{median:.2f}) — generuji se stálou referencí mluvčího")
                v.reference, v.druh_reference = stala, "mluvci"
                v.qa = sorted(set(v.qa) | {"identita"})
                self._synth(v, None)
            prubeh(0.8, f"Hlídač identity: mluvčí M{m + 1}, přegenerováno {len(ulet)}")
        self.z.uloz()

    def nadabuj(self, prubeh: Prubeh) -> None:
        self.mluvci_ref = refs.priprav(self.z)
        vety = [v for v in self.z.vety if (v.vyslov or v.cs).strip()]
        self.worker.start()
        try:
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
