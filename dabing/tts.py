"""Hlas verze A: OmniVoice klon ve workeru, hlídání identity a délky.

Postup (kvalita před rychlostí):
1. Každá věta se vygeneruje přirozeně s referencí „věta" (emoce z originálu)
   nebo „mluvčí".
2. Délka: co se vejde do slotu (věta + půjčka z mezery za ní), zůstane. Do 1,15×
   dostane OmniVoice `duration` = slot. Delší věty Codex dávkově zkrátí a pak
   se znovu měří. Nikdy rychleji než 1/0,85 přirozené řeči, zbytek dostane QA přesah.
3. Hlídač identity až na hotových větách: podobnost ECAPA s kotvou (stálou referencí)
   mluvčího. Věty, které z řady mluvčího vybočují, se namluví znovu a zůstane lepší pokus.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from . import refs, translate
from .config import (BLIZKE_JAZYKY, CIL_VYPLNENI, KOTVA_MIN_PODOBNOST, KOTVA_SEEDY, KOTVA_TEXT, MAX_POZICKA_S, MAX_ZPOMALENI, MIN_RYCHLOST,
                     PRAH_KRATKE_VETY, REFERENCE_HLASU, REZERVA_ZA_VETOU_S, WORKER_PYTHON,
                     WORKER_SKRIPT, ZRYCHLENI_BEZ_OBAV, offline_env)
from .job import Veta, Zakazka, otisk_tts

Log = Callable[[str], None]
Prubeh = Callable[[float, str], None]

KROKY = 32
IDENTITA_ODCHYLKA = 0.12
IDENTITA_MINIMUM = 0.35
TIMEOUT_VETY_S = 300.0
PRAH_PRODLOUZENI = 0.85   # hlas kratší než 85 % originálu → nechat text přeformulovat plněji
MIN_PRODLOUZENI_S = 2.0   # u kratších vět se ticho neřeší (stačí zpomalení)
MIN_TAKE_S = 0.1          # kratší take po ořezu = prázdný zvuk od OmniVoice
POKUSY_PRAZDNEHO_TAKE = 3  # kolikrát zkusit jiný seed (poslední bez ořezu OmniVoice), než věta zůstane bez hlasu
OPRAVY_PRESTRELU = 2      # kolikrát přegenerovat vynucenou větu, která vyšla mimo meze délky
TOLERANCE_PRESAHU_S = 0.02  # odchylka od mezí, která se ještě neřeší (slot už má rezervu 0,05 s)
# Úseky kroku „hlas" na ukazateli průběhu (0–1). Kroky po namluvení přegenerují i stovky vět
# (33min video 2026-09-29: prodloužení 243 vět, doladění ~260) — každý má vlastní úsek a hlásí
# se po větách, jinak ukazatel desítky minut stojí na jednom čísle.
USEK_NAMLUVENI = (0.0, 0.55)
USEK_ZKRACENI = (0.55, 0.62)
USEK_PRODLOUZENI = (0.62, 0.75)
USEK_DOLADENI = (0.75, 0.88)
USEK_IDENTITY = (0.88, 1.0)
POKUSY_IDENTITY = 2       # kolikrát znovu namluvit větu, které ujel hlas (zůstane lepší pokus)
# ECAPA dává krátkým větám přirozeně nižší podobnost (DevDay, moderátor: medián 0,45 u vět < 1 s,
# 0,68 u 1–1,5 s, 0,83 u ≥ 4 s) — proti jednomu mediánu mluvčího by se přegenerovaly skoro všechny
# krátké věty („Takže", „Děkuji") zbytečně a s falešným „HLAS NESEDÍ". Srovnává se proto s mediánem
# vět podobné délky (malé skupiny se sloučí se sousedními).
DELKY_IDENTITY_S = (1.0, 1.5, 2.5, 4.0)
MIN_SKUPINA_IDENTITY = 10
KRATKA_IDENTITA_S = 1.5   # kratší věty mají větší rozptyl podobnosti → větší odchylka
IDENTITA_ODCHYLKA_KRATKE = 0.18
MIN_DELKA_IDENTITA_S = 1.0  # kratší take: ECAPA šumí, absolutní minimum se nepoužije
# Take musí znít víc jako vlastní mluvčí než jako kterýkoli jiný (skutečné hlasy obou). Dobré
# Knapkovy věty měly náskok před moderátorem +0,4 až +0,6, klon z kotvy −0,2 až +0,1.
IDENTITA_NASKOK = 0.10
# Reference jiného mluvčího podobnější vlastní než tohle = nejspíš týž člověk rozdělený diarizací
# (odlišní mluvčí: ~0,3) → pro náskok se nepoužije.
IDENTITA_JINY_MLUVCI_MAX = 0.55


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


NASTUP_DB = -25.0      # první hláska: rámec nad −25 dB pod špičkou věty
NABEH_S = 0.10         # tolik před první hláskou nechat
SYKAVKA_DB = -48.0     # sykavka „s"/„š"/„f" před první hláskou: rámec nad −48 dB…
SYKAVKA_PODIL = 0.6    # …s více než 60 % energie nad 3 kHz (nádech a šum mají spektrum rozprostřené)
SYKAVKA_MEZERA = 8     # rámců (80 ms): závěr „t"/„p"/„k" mezi sykavkou a samohláskou („St-", „Sp-")


def _orizni_ticho(cesta: Path) -> tuple[float, float]:
    """Ořízne ticho na konci (−45 dB pod špičkou, 30 ms rezerva) a na začátku vše před první
    hláskou kromě NABEH_S — tichá sykavka těsně před ní („Sto", „Stačí") zůstane celá.
    OmniVoice často začíná 0,3–0,5 s tichého šumu či nádechu kolem −35 dB; dřívější −45dB
    práh ho nechával a čeština pak nastupovala o třetinu sekundy později než pusa řečníka
    (změřeno 2026-09-29, rozhovor Liquid). Vrací (délka po ořezu, kolik se uřízlo) v s."""
    x, sr = sf.read(cesta, dtype="float32")
    if x.size == 0:
        return 0.0, 0.0
    mono = x if x.ndim == 1 else x.mean(axis=1)
    ramec = int(0.01 * sr)
    n = len(mono) // ramec
    if n < 3:
        return len(x) / sr, 0.0
    ramce = mono[: n * ramec].reshape(n, ramec)
    energie = np.sqrt((ramce ** 2).mean(axis=1) + 1e-12)
    hlas = np.flatnonzero(energie > energie.max() * 10 ** (-45 / 20))
    nastup = np.flatnonzero(energie > energie.max() * 10 ** (NASTUP_DB / 20))
    if hlas.size == 0 or nastup.size == 0:
        return len(x) / sr, 0.0
    spektrum = np.abs(np.fft.rfft(ramce, axis=1)) ** 2
    vysoke = spektrum[:, np.fft.rfftfreq(ramec, 1 / sr) > 3000].sum(axis=1) / (spektrum.sum(axis=1) + 1e-20)
    sykavka = (energie > energie.max() * 10 ** (SYKAVKA_DB / 20)) & (vysoke > SYKAVKA_PODIL)
    i, mezera, prvni = nastup[0] - 1, 0, nastup[0]
    while i >= 0 and mezera <= SYKAVKA_MEZERA:
        if sykavka[i]:
            prvni, mezera = i, 0
        else:
            mezera += 1
        i -= 1
    a = max(0, hlas[0] * ramec - 2 * ramec,
            min(nastup[0] * ramec - int(NABEH_S * sr), (prvni - 2) * ramec))
    b = min(len(x), (hlas[-1] + 3) * ramec)
    if a > 0 or b < len(x):
        sf.write(cesta, x[a:b], sr, subtype="FLOAT")
    return (b - a) / sr, (len(x) - (b - a)) / sr


def slot(zakazka: Zakazka, i: int) -> float:
    v = zakazka.vety[i]
    konec_videa = zakazka.delka_s or v.end + MAX_POZICKA_S
    dalsi = zakazka.vety[i + 1].start if i + 1 < len(zakazka.vety) else min(v.end + MAX_POZICKA_S, konec_videa)
    return v.delka + max(0.0, min(dalsi - v.end - REZERVA_ZA_VETOU_S, MAX_POZICKA_S))


ZNAKY_JAZYKA = {"cs": set("řěů"), "sk": set("äôľĺŕ")}  # písmena, která má jen jeden z jazyků


def zdrojovy_jazyk(z: Zakazka) -> str:
    """Jazyk originálu; u „Automaticky" (výchozí v labu) češtinu a slovenštinu pozná podle
    písmen v přepisu — jinak by se u českého videa do slovenštiny klonovalo z kotvy."""
    if z.jazyk_zdroje != "auto":
        return z.jazyk_zdroje
    text = " ".join(v.en for v in z.vety).lower()
    pocty = {j: sum(text.count(c) for c in znaky) for j, znaky in ZNAKY_JAZYKA.items()}
    jazyk, pocet = max(pocty.items(), key=lambda p: p[1])
    return jazyk if pocet >= 20 and pocet >= 5 * min(pocty.values()) else "auto"


def blizke_jazyky(z: Zakazka) -> bool:
    """Originál a dabing v blízkých jazycích (cs ↔ sk) → klon ze skutečného hlasu, ne z kotvy."""
    zdroj = zdrojovy_jazyk(z)
    return any({zdroj, z.jazyk} <= dvojice for dvojice in BLIZKE_JAZYKY) and zdroj != z.jazyk


def ocekavana_podobnost(vety: list[Veta]) -> dict[int, float]:
    """Pro každou větu medián změřené podobnosti vět mluvčího podobné délky hlasu."""
    def skupina(v: Veta) -> int:
        return sum(v.vysledna_s >= h for h in DELKY_IDENTITY_S)

    skupiny: dict[int, list[float]] = {}
    for v in vety:
        if v.identita is not None:
            skupiny.setdefault(skupina(v), []).append(v.identita)
    vysledek: dict[int, float] = {}
    for v in vety:
        s = skupina(v)
        hodnoty = list(skupiny.get(s, []))
        for vzdalenost in range(1, len(DELKY_IDENTITY_S) + 1):
            if len(hodnoty) >= MIN_SKUPINA_IDENTITY:
                break
            hodnoty += skupiny.get(s - vzdalenost, []) + skupiny.get(s + vzdalenost, [])
        if hodnoty:
            vysledek[v.id] = float(np.median(hodnoty))
    return vysledek


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
            delka, _ = _orizni_ticho(cil)  # cache ze starší verze ořezu dořízne (jinak beze změny)
            if delka >= MIN_TAKE_S:  # prázdný take z dřívějška se generuje znovu
                if vynucena:
                    v.vysledna_s = delka
                else:
                    v.prirozena_s = v.vysledna_s = delka
                return delka
        cil.parent.mkdir(parents=True, exist_ok=True)
        for pokus_take in range(POKUSY_PRAZDNEHO_TAKE):
            zadani = {"op": "synth", "id": v.id, "text": v.vyslov or v.cs, "jazyk": self.z.jazyk,
                      "ref_audio": str(reference), "ref_text": refs.text_reference(self.z, v.reference),
                      "kroky": KROKY, "seed": v.seed, "vystup": str(cil)}
            if vynucena:
                # duration OmniVoice počítá i úvodní šum/nádech, který ořez odstraní — přidat ho, ať
                # věta po ořezu opravdu trvá `vynucena` (jinak vycházela v mediánu o 0,3–0,6 s kratší)
                zadani["duration"] = round(vynucena + v.rezie_s, 3)
            if pokus_take == POKUSY_PRAZDNEHO_TAKE - 1:
                # poslední pokus bez ořezu ticha v OmniVoice (pevný práh −50 dBFS): tiše vyslovené
                # slovo („alebo") uřízl celé u všech seedů i s pevnou délkou; náš ořez je relativní
                zadani["orez"] = False
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
            delka, orez = _orizni_ticho(cil)
            if delka >= MIN_TAKE_S:
                break
            # OmniVoice u jednoslovných vět („že", „keď", „alebo") občas vrátí prázdný zvuk
            # (2026-09-29: 5 z 330 vět) — věta by v dabingu chyběla. Zkusit jiný seed.
            self.log(f"Věta {v.id}: OmniVoice vrátil prázdný zvuk — zkouším jiný pokus")
            v.seed += 1
            otisk = otisk_tts(v, self.z.jazyk, KROKY, reference.stat().st_mtime)
        else:
            self.log(f"Věta {v.id}: ani {POKUSY_PRAZDNEHO_TAKE} pokusy nedaly zvuk — zůstane bez hlasu")
        v.tts = str(cil.relative_to(self.z.slozka))
        v.tts_otisk = otisk
        if vynucena:
            v.vysledna_s = delka
        else:
            v.prirozena_s = v.vysledna_s = delka
            if zadani.get("orez", True):  # bez ořezu OmniVoice by „režie" zahrnula i ticho na konci
                v.rezie_s = round(orez, 3)
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
                delka, _ = _orizni_ticho(pokus)
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

    def _po_vetach(self, polozky: list, prubeh: Prubeh, usek: tuple[float, float], text: str):
        """Projde položky a každých 5 nahlásí průběh v úseku `usek` s odhadem zbývajícího času
        a uloží zakázku (ukazatel nestojí a pád neztratí změřené délky)."""
        od, do = usek
        start = time.monotonic()
        for i, polozka in enumerate(polozky):
            yield polozka
            if i % 5 == 4 or i == len(polozky) - 1:
                zbyva = (time.monotonic() - start) / (i + 1) * (len(polozky) - i - 1)
                prubeh(od + (do - od) * (i + 1) / len(polozky),
                       f"{text} {i + 1}/{len(polozky)} (zbývá ~{zbyva / 60:.0f} min)")
                self.z.uloz()

    def _podobnost(self, v: Veta, srovnat: str) -> float | None:
        return self._podobnost_cest(v.tts, srovnat, f"Věta {v.id}")

    def _podobnost_cest(self, a: str, b: str, kde: str = "Kontrola hlasu") -> float | None:
        """Podobnost hlasu (ECAPA) dvou nahrávek zakázky; při pádu workeru None."""
        try:
            odpoved = self.worker.dotaz({"op": "identita", "a": str(self.z.slozka / a),
                                         "b": str(self.z.slozka / b)}, timeout=120.0)
        except WorkerSpadl as chyba:  # kontrola hlasu nesmí shodit hotový dabing
            self.log(f"{kde}: měření hlasu selhalo ({chyba}) — restartuji worker")
            self.worker.zabij()
            self.worker.start()
            return None
        return odpoved.get("podobnost") if odpoved.get("ok") else None

    def _snimek(self, v: Veta, soubor: Path) -> dict:
        """Záloha hotového take (zvuk + pole věty), ať jde vrátit, když nový pokus dopadne hůř."""
        shutil.copyfile(self.z.slozka / v.tts, soubor)
        return {k: list(getattr(v, k)) if k == "qa" else getattr(v, k)
                for k in ("seed", "reference", "druh_reference", "tts_otisk", "vynucena_s", "vysledna_s",
                          "prirozena_s", "rezie_s", "pomer", "qa", "identita")}

    def _identita(self, prubeh: Prubeh) -> None:
        """Hlídač identity až na hotových větách. Doladění délek přegeneruje stovky vět a hlas
        nového pokusu může ujet (2026-09-29: 54 z 275 vět pplk. Knapka, třeba 0,79 → 0,31, když
        se hlídalo před doladěním). Věta, která hlasem vybočuje z řady mluvčího, se namluví znovu
        (jiný seed; emoční reference → kotva nebo stálá), znovu dostane délku do slotu a zůstane
        lepší pokus — nejdřív bez přesahu do další věty, pak podle podobnosti."""
        indexy = {v.id: i for i, v in enumerate(self.z.vety)}
        mluvci = list(self.mluvci_ref.items())
        od, do = USEK_IDENTITY
        for k, (m, stala) in enumerate(mluvci):
            kotva = self.kotvy.get(m)
            vety = [v for v in self.z.vety if v.mluvci == m and v.druh_reference in ("veta", "kotva", "mluvci")
                    and v.tts and (v.vyslov or v.cs).strip()]  # věta bez textu (bez dabingu) se nenamlouvá
            if len(vety) < 3:
                continue
            zacatek, konec = od + (do - od) * k / len(mluvci), od + (do - od) * (k + 1) / len(mluvci)
            stred = (zacatek + konec) / 2
            # skutečné hlasy ostatních mluvčích — jen těch, kteří znějí opravdu jinak: když diarizace
            # rozdělí jednoho člověka na dva mluvčí (nebo je reference jiného mluvčího přeslechnutá),
            # zněla by každá věta „jako jiný mluvčí" a kontrola by ji zbytečně přegenerovala
            cizi = []
            for n, r in self.mluvci_ref.items():
                if n == m:
                    continue
                paru = self._podobnost_cest(stala, r)
                if paru is not None and paru < IDENTITA_JINY_MLUVCI_MAX:
                    cizi.append(r)
                else:
                    self.log(f"Mluvčí M{m + 1} a M{n + 1} znějí podobně ({paru}) — pro kontrolu "
                             "záměny hlasu je nesrovnávám.")
            naskoky: dict[int, float | None] = {}

            def srovnat(v: Veta) -> str:
                return kotva if v.druh_reference == "kotva" and kotva else stala

            def zmer(v: Veta) -> None:
                """Podobnost s vlastní referencí a náskok před nejpodobnějším jiným mluvčím
                (obojí se skutečnými hlasy): klon z kotvy u Knapka zněl jako moderátor, ač
                podobnost s vlastní kotvou vypadala v řadě (2026-09-30)."""
                v.identita = self._podobnost(v, srovnat(v))
                naskoky[v.id] = None
                if cizi and v.identita is not None:
                    vlastni = v.identita if srovnat(v) == stala else self._podobnost(v, stala)
                    jine = [p for p in (self._podobnost(v, r) for r in cizi) if p is not None]
                    if vlastni is not None and jine:
                        naskoky[v.id] = vlastni - max(jine)

            def zni_jako_jiny(v: Veta) -> bool:
                return naskoky.get(v.id) is not None and naskoky[v.id] < IDENTITA_NASKOK

            for v in self._po_vetach(vety, prubeh, (zacatek, stred), f"Kontrola hlasu M{m + 1}: měřím"):
                zmer(v)
                v.qa = [q for q in v.qa if q not in ("hlas_ujel", "identita")]  # značky z dřívějška
            ocekavana = ocekavana_podobnost(vety)
            if not ocekavana:
                continue

            def stav(v: Veta) -> dict:
                return {"id": v.id, "qa": list(v.qa), "identita": v.identita, "vysledna": v.vysledna_s,
                        "naskok": naskoky.get(v.id)}

            def ujel_stav(s: dict) -> bool:
                """Podobnost take vybočuje z řady vět podobné délky (a nemá být pod absolutním
                minimem — to jen u vět, na kterých ECAPA už něco měří), nebo take zní skoro
                stejně jako jiný mluvčí."""
                if s["identita"] is None or s["id"] not in ocekavana:
                    return False
                odchylka = IDENTITA_ODCHYLKA_KRATKE if s["vysledna"] < KRATKA_IDENTITA_S else IDENTITA_ODCHYLKA
                return (s["identita"] < ocekavana[s["id"]] - odchylka
                        or (s["vysledna"] >= MIN_DELKA_IDENTITA_S and s["identita"] < IDENTITA_MINIMUM)
                        or (s["naskok"] is not None and s["naskok"] < IDENTITA_NASKOK))

            def ujel(v: Veta) -> bool:
                return ujel_stav(stav(v))

            def klic(s: dict) -> tuple[bool, bool, bool, bool, float]:
                """změřená > bez přesahu > projde celou kontrolou > nezní jako jiný mluvčí > podobnost"""
                identita, naskok = s["identita"], s["naskok"]
                return (identita is not None, "presah" not in s["qa"], not ujel_stav(s),
                        naskok is None or naskok >= IDENTITA_NASKOK,
                        identita if identita is not None else -1.0)

            ulet = [v for v in vety if ujel(v)]
            for v in self._po_vetach(ulet, prubeh, (stred, konec), f"Kontrola hlasu M{m + 1}: opravuji"):
                duvod = (f", náskok před jiným mluvčím {naskoky[v.id]:+.2f}" if zni_jako_jiny(v) else "")
                self.log(f"Věta {v.id}: hlas vybočil (podobnost {v.identita:.2f} vs {ocekavana[v.id]:.2f} "
                         f"u vět podobné délky{duvod}) — namlouvám znovu")
                zaloha = self.z.cesta(f"tts/veta_{v.id:04d}.identita.wav")
                chyba: RuntimeError | None = None
                try:
                    nejlepsi, nejlepsi_stav = self._snimek(v, zaloha), stav(v)
                    try:
                        for _ in range(POKUSY_IDENTITY):
                            if v.druh_reference == "veta":
                                v.reference, v.druh_reference = (kotva, "kotva") if kotva else (stala, "mluvci")
                            else:
                                v.seed += 1
                            self._synth(v, None)
                            self.dolad(v, slot(self.z, indexy[v.id]))
                            v.qa = sorted(set(v.qa) | {"identita"})
                            zmer(v)
                            if klic(stav(v)) > klic(nejlepsi_stav):
                                nejlepsi, nejlepsi_stav = self._snimek(v, zaloha), stav(v)
                            if v.identita is not None and not ujel(v) and "presah" not in v.qa:
                                break
                    except RuntimeError as e:  # worker selhal (nebo zrušeno) — vrátit nejlepší pokus
                        chyba = e
                    if chyba is not None or (v.seed, v.reference, v.tts_otisk) != (
                            nejlepsi["seed"], nejlepsi["reference"], nejlepsi["tts_otisk"]):
                        shutil.copyfile(zaloha, self.z.slozka / v.tts)
                        for pole, hodnota in nejlepsi.items():
                            setattr(v, pole, hodnota)
                        naskoky[v.id] = nejlepsi_stav["naskok"]
                finally:
                    zaloha.unlink(missing_ok=True)
                if ujel(v):
                    v.qa = [q for q in v.qa if q != "identita"] + ["hlas_ujel"]
                self.z.uloz()  # take na disku a job.json vždy spolu
                if isinstance(chyba, Zruseno):
                    raise chyba
                if chyba is not None:
                    self.log(f"Věta {v.id}: nový pokus selhal ({chyba}) — nechávám lepší verzi")
            opraveno = sum(1 for v in ulet if not ujel(v))
            prubeh(konec, f"Kontrola hlasu M{m + 1}: opraveno {opraveno} z {len(ulet)}")
            if len(ulet) > opraveno:
                self.log(f"Mluvčí M{m + 1}: {len(ulet) - opraveno} vět má pořád hlas mimo řadu — "
                         "jsou v tabulce označené, poslechni si je.")
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
        blizke = blizke_jazyky(self.z)
        if blizke:
            self.log(f"Blízký jazyk ({zdrojovy_jazyk(self.z)} → {self.z.jazyk}): hlas klonuji ze skutečného "
                     "hlasu mluvčího, kotva jen změří rychlost řeči.")
        for v in self.z.vety:
            if blizke and v.mluvci in self.mluvci_ref:
                v.reference, v.druh_reference = self.mluvci_ref[v.mluvci], "mluvci"
            elif v.mluvci in self.kotvy:
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
            for v in self._po_vetach(vety, prubeh, USEK_NAMLUVENI, "Hlas"):
                self._synth(v, None)
            self._delky(prubeh)
            self._identita(prubeh)  # až po doladění délek — to přegeneruje stovky vět
        finally:
            self.worker.zavri()
        self.z.hotovo("hlas")

    def _delky(self, prubeh: Prubeh) -> None:
        indexy = {v.id: i for i, v in enumerate(self.z.vety)}
        dlouhe: list[tuple[Veta, float]] = []
        for v in self.z.vety:
            if not v.tts or not (v.vyslov or v.cs).strip():
                continue
            s = slot(self.z, indexy[v.id])
            v.pomer = round(v.prirozena_s / max(v.delka, 0.01), 3)
            if v.prirozena_s > s * ZRYCHLENI_BEZ_OBAV and not v.upraveno:
                dlouhe.append((v, s))
        if dlouhe:
            prubeh(USEK_ZKRACENI[0], f"Zkracuji {len(dlouhe)} vět, které se nevejdou (Codex)…")
            for v, s in dlouhe:
                v.max_znaku = max(8, int(len(v.vyslov or v.cs) * s / v.prirozena_s * 0.95))
            translate.zkrat(self.z, [v for v, _ in dlouhe], self.log)
            for v, _ in self._po_vetach(dlouhe, prubeh, USEK_ZKRACENI, "Zkracuji věty: hlas"):
                self._synth(v, None)
        # a naopak: věta výrazně kratší než originál → po ní by zůstalo ticho, zatímco řečník
        # na obraze mluví. Změřená délka hlasu přesně řekne, kolik textu přidat.
        kratke = [(v, slot(self.z, indexy[v.id])) for v in self.z.vety
                  if v.tts and (v.vyslov or v.cs).strip() and not v.upraveno and v.delka >= MIN_PRODLOUZENI_S
                  and v.prirozena_s < v.delka * PRAH_PRODLOUZENI]
        if kratke:
            prubeh(USEK_PRODLOUZENI[0], f"Prodlužuji {len(kratke)} vět, po kterých by zůstalo ticho (Codex)…")
            puvodni = {v.id: v.vyslov or v.cs for v, _ in kratke}
            for v, s in kratke:
                znaku_za_s = len(puvodni[v.id]) / max(v.prirozena_s, 0.3)  # tahle věta, tenhle hlas
                v.cil_znaku = int(v.delka * CIL_VYPLNENI * znaku_za_s)
                v.max_znaku = max(v.cil_znaku, int(s * znaku_za_s * 0.95))
            translate.prodluz(self.z, [v for v, _ in kratke], self.log)
            zmenene = [v for v, _ in kratke if (v.vyslov or v.cs) != puvodni[v.id]]
            for v in self._po_vetach(zmenene, prubeh, USEK_PRODLOUZENI, "Prodlužuji věty: hlas"):
                self._synth(v, None)
        prubeh(USEK_DOLADENI[0], "Dolaďuji délky vět…")
        for v in self._po_vetach(self.z.vety, prubeh, USEK_DOLADENI, "Dolaďuji délky vět"):
            if not v.tts or not (v.vyslov or v.cs).strip():
                continue
            self.dolad(v, slot(self.z, indexy[v.id]))
        self.z.uloz()

    def dolad(self, v: Veta, s: float) -> None:
        """Délka jedné věty do slotu `s`: krátkou zpomalit na délku originálu (pusa), dlouhou
        zrychlit na slot; značka zpomaleno/zrychleno/presah podle změřené délky."""
        v.pomer = round(v.prirozena_s / max(v.delka, 0.01), 3)
        v.qa = [q for q in v.qa if q not in ("zrychleno", "presah", "zpomaleno")]
        if v.prirozena_s < v.delka * PRAH_KRATKE_VETY:
            # krátká věta: mírně zpomalit, ať řeč pokryje čas, kdy mluví originál
            cil = min(v.delka, v.prirozena_s / MAX_ZPOMALENI)  # celá délka originálu (pusa)
            if cil > v.prirozena_s * 1.03:
                if self._vynut(v, cil, s, v.prirozena_s * 1.02):
                    v.qa.append("zpomaleno")
                    return
                # žádný pokus nebyl zároveň pomalejší než přirozená a v slotu → přirozená (vejde se)
                self._synth(v, None)
        if v.prirozena_s <= s + TOLERANCE_PRESAHU_S:
            v.vynucena_s = None
            v.vysledna_s = v.prirozena_s
            return
        if v.prirozena_s / s <= 1 / MIN_RYCHLOST:
            self._vynut(v, s, s, v.prirozena_s * MIN_RYCHLOST)
        else:
            self._synth(v, v.prirozena_s * MIN_RYCHLOST)  # ani nejvyšší zrychlení na slot nestačí
        # značka podle změřené délky (vynucená verze občas vyjde kratší, než se chtělo)
        v.qa.append("zrychleno" if v.vysledna_s <= s + TOLERANCE_PRESAHU_S else "presah")

    def _vynut(self, v: Veta, cil: float, s: float, nejkratsi: float) -> bool:
        """Vynutí délku `cil` a ohlídá výsledek: věta nesmí přesáhnout slot `s` do další věty
        (dva hlasy naráz zní jako seknutí) ani vyjít kratší než `nejkratsi` (rychlejší, než je
        dovoleno). Úvodní šum vynucené verze se pokus od pokusu liší (`rezie_s` je změřená na
        přirozené; rozhovor Liquid 2026-09-29: −0,95 až +0,39 s), proto se každý pokus změří
        a mimo meze se věta až dvakrát přegeneruje s cílem posunutým o chybu posledního pokusu.
        Když se netrefí žádný, zůstane nejlepší (bez přesahu, nejblíž cíli). Vrátí, zda je v mezích."""
        chtena = min(cil, s - TOLERANCE_PRESAHU_S)

        def v_mezich() -> bool:
            return nejkratsi - TOLERANCE_PRESAHU_S <= v.vysledna_s <= s + TOLERANCE_PRESAHU_S

        def horsi(delka: float) -> tuple[bool, float]:  # nejdřív přesah do další věty, pak odchylka
            return delka > s + TOLERANCE_PRESAHU_S, abs(delka - chtena)

        zaloha = self.z.cesta(f"tts/veta_{v.id:04d}.nejlepsi.wav")
        nejlepsi: tuple[float, float, str, int] | None = None
        try:
            self._synth(v, cil)
            for pokus in range(OPRAVY_PRESTRELU + 1):
                if v_mezich():
                    return True
                if nejlepsi is None or horsi(v.vysledna_s) < horsi(nejlepsi[1]):
                    nejlepsi = (v.vynucena_s, v.vysledna_s, v.tts_otisk, v.seed)  # _synth mění seed u prázdného take
                    shutil.copyfile(self.z.slozka / v.tts, zaloha)
                dalsi = max(0.3, chtena - (v.vysledna_s - cil))
                if pokus == OPRAVY_PRESTRELU or abs(dalsi - cil) < 0.01:  # skoro stejná délka = tentýž take
                    break
                cil = dalsi
                self._synth(v, cil)
            if nejlepsi and (v.vynucena_s, v.vysledna_s) != nejlepsi[:2]:
                os.replace(zaloha, self.z.slozka / v.tts)
                v.vynucena_s, v.vysledna_s, v.tts_otisk, v.seed = nejlepsi
            return v_mezich()
        finally:
            zaloha.unlink(missing_ok=True)
