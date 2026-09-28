"""Orchestrace dabingu verze A.

Těžké modely (Demucs, Parakeet, Nemotron) běží jako podprocesy tohoto venv
(`python -m dabing.stages.<krok>`), OmniVoice ve vlastním workeru. Všechno ve
stejné skupině procesů jako lab, takže je portál umí ukončit. Hotové kroky se
přeskakují — zakázka jde navázat a po úpravě textu se generuje jen změněné.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import media, mix, segmenty, translate
from .config import JOBS, LAB, MODEL_DIALOG_CHECKPOINT, PYTHON, offline_env
from .job import Zakazka, id_ze_souboru
from .tts import Hlas, Zruseno

Log = Callable[[str], None]
Prubeh = Callable[[float, str], None]

# podíly jednotlivých kroků na celkovém průběhu
FAZE = [("stazeni", 0.00, 0.05), ("separace", 0.05, 0.09), ("pozadi", 0.09, 0.24),
        ("prepis", 0.24, 0.31), ("mluvci", 0.31, 0.38), ("vety", 0.38, 0.40), ("preklad", 0.40, 0.50),
        ("hlas", 0.50, 0.88), ("kontrola", 0.88, 0.91), ("detekce", 0.91, 0.92),
        ("export", 0.92, 1.00)]

# Nad tímhle podílem chybných znaků ve zpětném přepisu je věta nejspíš zamumlaná
# nebo useknutá (cizí jména psaná foneticky zvednou CER jen mírně).
PRAH_CER = 0.30


class Pipeline:
    def __init__(self, log: Log, prubeh: Prubeh):
        self._log = log
        self._prubeh = prubeh
        self.zrusit = threading.Event()
        self._proces: subprocess.Popen | None = None

    # ----------------------------------------------------------- průběh
    def _faze(self, jmeno: str) -> Prubeh:
        _, od, do = next(f for f in FAZE if f[0] == jmeno)

        def hlaseni(p: float, zprava: str) -> None:
            self._prubeh(od + (do - od) * max(0.0, min(1.0, p)), zprava)
        return hlaseni

    def log(self, zprava: str) -> None:
        self._log(time.strftime("%H:%M:%S ") + zprava)

    def _kontrola(self) -> None:
        if self.zrusit.is_set():
            raise Zruseno()

    def zrus(self) -> None:
        self.zrusit.set()
        if self._proces is not None and self._proces.poll() is None:
            self._proces.terminate()

    # ----------------------------------------------------------- kroky
    def _krok(self, modul: str, zakazka: Zakazka, faze: str, argumenty: tuple[str, ...] = (),
              env_navic: dict[str, str] | None = None) -> None:
        self._kontrola()
        hlaseni = self._faze(faze)
        self._proces = subprocess.Popen(
            [str(PYTHON), "-m", f"dabing.stages.{modul}", str(zakazka.slozka), *argumenty],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            cwd=str(LAB), env={**offline_env(), **(env_navic or {})},
        )
        posledni: list[str] = []
        zaloguj = ""
        assert self._proces.stdout is not None
        for radek in self._proces.stdout:
            radek = radek.rstrip()
            if radek.startswith("@@ "):
                data = json.loads(radek[3:])
                hlaseni(data["p"], data.get("zprava", ""))
                if data.get("zprava") and data["zprava"] != zaloguj:
                    zaloguj = data["zprava"]
                    self.log(zaloguj)
            elif radek:
                posledni = (posledni + [radek])[-15:]
        kod = self._proces.wait()
        self._proces = None
        self._kontrola()
        if kod != 0:
            raise RuntimeError(f"Krok {modul} selhal (kód {kod}):\n" + "\n".join(posledni[-8:]))

    def priprav(self, zdroj: str, jazyk: str, prekladac: str, pocet_mluvcich: int | None,
                jazyk_zdroje: str = "auto") -> Zakazka:
        """Založí nebo načte zakázku a stáhne/zkopíruje zdroj."""
        hlaseni = self._faze("stazeni")
        cesta = Path(zdroj).expanduser()
        if cesta.exists():
            id_ = id_ze_souboru(cesta)
            nazev, delka_s = cesta.stem, media.delka(cesta)
        else:
            hlaseni(0.1, "Zjišťuji video…")
            info = media.info(zdroj)
            id_, nazev, delka_s = info["id"], info["nazev"], info["delka_s"]
        slozka = JOBS / id_
        if (slozka / "job.json").exists():
            z = Zakazka.nacti(slozka)
            if z.jazyk != jazyk or z.prekladac != prekladac or z.jazyk_zdroje != jazyk_zdroje:
                self.log("Změnil se jazyk nebo režim — překládám znovu.")
                for v in z.vety:
                    if not v.upraveno:
                        v.cs = ""
                z.kroky.pop("preklad", None)
                z.kroky.pop("hlas", None)
            z.jazyk, z.prekladac, z.pocet_mluvcich = jazyk, prekladac, pocet_mluvcich
            z.jazyk_zdroje = jazyk_zdroje
        else:
            z = Zakazka(id=id_, zdroj=zdroj, nazev=nazev, delka_s=delka_s, jazyk=jazyk,
                        jazyk_zdroje=jazyk_zdroje,
                        prekladac=prekladac, pocet_mluvcich=pocet_mluvcich,
                        vytvoreno=time.strftime("%Y-%m-%d %H:%M:%S"))
        z.slozka.mkdir(parents=True, exist_ok=True)
        z.uloz()
        self.log(f"Zakázka {z.id}: {z.nazev} ({z.delka_s / 60:.1f} min)")

        if cesta.exists():
            cil = z.cesta("source" + cesta.suffix.lower())
            if not cil.exists():
                shutil.copy2(cesta, cil)
            zdroj_media = cil
        else:
            hlaseni(0.2, "Stahuji video…")
            zdroj_media = media.stahni(zdroj, z.slozka, lambda r: None)
        hlaseni(0.9, "Připravuji zvuk…")
        media.extrahuj_zvuk(zdroj_media, z.cesta("audio_hq.wav"), 44100, 2)
        if not z.delka_s:
            z.delka_s = media.delka(z.cesta("audio_hq.wav"))
        if zdroj_media.name != "source.mp4" and media.ma_video(zdroj_media):
            # jiný kontejner než mp4: export si video převezme z tohoto souboru
            odkaz = z.cesta("source.mp4")
            if not odkaz.exists():
                media.spust([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i",
                             str(zdroj_media), "-c", "copy", str(odkaz)])
        z.hotovo("stazeni")
        return z

    def analyzuj(self, z: Zakazka) -> None:
        self._krok("separate", z, "separace")
        self._efekty(z)
        self._krok("asr_local", z, "prepis")
        if z.pocet_mluvcich != 1:
            self._krok("diar", z, "mluvci")
        self._kontrola()
        if "vety" not in z.kroky or not z.vety or self._novejsi_nez_vety(z):
            self._faze("vety")(0.5, "Skládám věty…")
            segmenty.uloz_vety(z)
            self.log(f"{len(z.vety)} vět, mluvčích: {len({v.mluvci for v in z.vety})}")

    def _efekty(self, z: Zakazka, faze: str = "pozadi") -> None:
        """BandIt najde v hlasové stopě Demucsu jásot, potlesk a smích; mix je vrátí do pozadí.
        Hotový krok se přeskočí. Selhání dabing nezastaví, jen zmizí jásot přes řeč."""
        if not MODEL_DIALOG_CHECKPOINT.exists():
            self.log("BandIt chybí (modely/bandit_plus) — jásot přes řeč zmizí spolu s hlasem.")
            return
        for env in (None, {"DABING_DIALOG_CPU": "1"}):  # když grafika spadne tvrdě, znovu na CPU
            try:
                self._krok("dialog", z, faze, ("hlas", "--efekty"), env)
                return
            except Zruseno:
                raise
            except RuntimeError as chyba:
                self.log(f"BandIt selhal{'' if env is None else ' i na procesoru'}: {chyba}")
        self.log("Jásot přes řeč zmizí spolu s hlasem (BandIt neproběhl).")

    @staticmethod
    def _novejsi_nez_vety(z: Zakazka) -> bool:
        """Přepis nebo mluvčí vznikli znovu až po složení vět → věty složit znovu."""
        slozeno = time.mktime(time.strptime(z.kroky["vety"], "%Y-%m-%d %H:%M:%S"))
        return any(z.cesta(j).exists() and z.cesta(j).stat().st_mtime > slozeno + 1
                   for j in ("asr.json", "diar.json"))

    def preloz(self, z: Zakazka) -> None:
        self._kontrola()
        if all(v.cs for v in z.vety) and "preklad" in z.kroky:
            return
        kdo = "Codex" if z.prekladac == "codex" else "lokální model v LM Studiu"
        self.log(f"Překládám ({kdo})…")
        translate.preloz(z, self.log, self._faze("preklad"))
        if z.prekladac == "codex" and z.codex:
            self.log(f"Codex: {z.codex.get('turny')} turnů, kvóta "
                     f"{z.codex.get('kvota_pred_%')} % → {z.codex.get('kvota_po_%')} %")

    def nadabuj(self, z: Zakazka) -> dict[str, Path]:
        self._kontrola()
        Hlas(z, self.log, self.zrusit).nadabuj(self._faze("hlas"))
        self._kontrola()
        self._krok("qa_zpet", z, "kontrola")
        self._oznac_nesrozumitelne(z)
        self._efekty(z, "detekce")  # u starších zakázek (Přegenerovat) se dopočítají, jinak přeskočí
        try:
            self._krok("vad", z, "detekce")
        except Zruseno:
            raise
        except RuntimeError as chyba:  # bez VAD mix pojede jen podle časů slov
            self.log(f"Detekce řeči selhala, podklad se řídí jen časy slov: {chyba}")
        self._kontrola()
        self._faze("export")(0.1, "Míchám a exportuji…")
        soubory = mix.sloz(z, self.log)
        self._faze("export")(1.0, "Hotovo")
        return soubory

    def _oznac_nesrozumitelne(self, z: Zakazka) -> None:
        cesta = z.cesta("qa_zpet.json")
        if not cesta.exists():
            return
        data = json.loads(cesta.read_text(encoding="utf-8"))
        spatne = 0
        for v in z.vety:
            v.qa = [q for q in v.qa if q != "nesrozumitelne"]
            zaznam = data.get(str(v.id))
            if zaznam and zaznam["cer"] > PRAH_CER:
                v.qa.append("nesrozumitelne")
                spatne += 1
        z.uloz()
        if spatne:
            self.log(f"Kontrola: {spatne} vět je špatně srozumitelných — zkontroluj je v tabulce.")

    def cely(self, zdroj: str, jazyk: str, prekladac: str, pocet_mluvcich: int | None,
             jazyk_zdroje: str = "auto") -> tuple[Zakazka, dict[str, Path]]:
        z = self.priprav(zdroj, jazyk, prekladac, pocet_mluvcich, jazyk_zdroje)
        self.analyzuj(z)
        self.preloz(z)
        return z, self.nadabuj(z)


def test_mixu(id_zakazky: str) -> dict[str, Path]:
    """„Identity dabing": místo TTS vloží původní hlas. Výsledek má znít jako originál."""
    z = Zakazka.nacti(id_zakazky)
    vad = subprocess.run([str(PYTHON), "-m", "dabing.stages.vad", str(z.slozka)], cwd=str(LAB),
                         env=offline_env(), capture_output=True, text=True)
    if vad.returncode != 0:
        print("Detekce řeči selhala, podklad se řídí jen časy slov:", vad.stdout[-400:])
    return mix.sloz(z, print, identita=True)
