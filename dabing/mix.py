"""Složení dabingu: podklad bez hlasu mluvčího, český hlas, hlasitost a export.

Podklad = originál − g·hlas (maska g z maska.py). Kde mluvčí nemluví, je g = 0
a hraje originál beze změny, takže publikum, hudba i ruchy zůstanou. V řeči se
odečte jen odhad hlasu mluvčího: hlasová stopa Demucsu bez toho, co v ní BandIt
označil jako efekty (jásot, potlesk, smích) — ty tak zůstanou i přes řeč. Zpěv
pod řečí zatím ne (stopa hudby z BandItu si plete kusy řeči, nepoužívá se).

Hlasitost: podklad se nikdy neztišuje ani nekomprimuje. Každá česká věta se srovná
s původním hlasem v tom místě (cíl nejvýš ±4 dB od typické hlasitosti mluvčího, zisk
nejvýš ±9 dB od typického zisku) a ztiší se jen tehdy, když by s podkladem přesáhla
−1 dBFS nebo špičku originálu v tom místě. Celková hlasitost se jen měří.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from scipy.ndimage import minimum_filter1d, uniform_filter1d

from . import maska
from .config import (DIALOG_VERZE, MAX_ODCHYLKA_MLUVCIHO_DB, MAX_ZISK_VETY_DB, MODEL_DIALOG_CHECKPOINT,
                     SR_MIX, STROP_SPICKY_DBFS, VAD_PRAH)
from .job import Veta, Zakazka, bezpecne_jmeno
from .media import FFMPEG, export_mp4, ma_video, srt

Log = Callable[[str], None]
NABEH_S = 0.01
RAMEC_UROVNE_S = 0.02
UZKY_HLAS_DB = -20.0      # side/mid hlasové stopy: mluvčí z ozvučení je úzký, jásot široký
TICHO_DBFS = -60.0        # úroveň pod tímhle je ticho, ne měřitelná řeč
MAX_POD_NEJHLASITEJSI_DB = 30.0
MIN_VET_PRO_SVORKU = 3
BLOK_SPICKY = 480         # 10 ms při 48 kHz
STROP_MAX = 10 ** (-0.5 / 20)  # rezerva pro true peak a AAC
MIN_ZISK_STROPU = 0.5          # kvůli špičce se celá věta ztiší nejvýš o 6 dB
OBALKA_SIRKA_S = 0.01          # místní ztišení hlasu kolem špičky podkladu (šířka minima ≥ vyhlazení)
OBALKA_HLADKA_S = 0.01
MIN_OBALKA = 0.25              # místně nejvýš o dalších 12 dB; zbytek přetečení srovná ořez při zápisu


def _nacti(cesta: Path, sr: int, kanaly: int) -> np.ndarray:
    x, puvodni = sf.read(cesta, dtype="float32", always_2d=True)
    if puvodni != sr:
        x = soxr.resample(x, puvodni, sr, quality="HQ").astype(np.float32)
    if kanaly == 1:
        return x.mean(axis=1)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return np.ascontiguousarray(x[:, :2])


def _rms_ramcu(x: np.ndarray) -> np.ndarray:
    r = int(RAMEC_UROVNE_S * SR_MIX)
    n = len(x) // r
    return np.sqrt((x[:n * r].reshape(n, r) ** 2).mean(axis=1) + 1e-12)


def _aktivni_rms(r: np.ndarray) -> float:
    """RMS jen z rámců s řečí (ticho mezi slovy by hlasitost podhodnotilo). Ticho = 0."""
    if r.size == 0:
        return 0.0
    aktivni = r[r > r.max() * 10 ** (-30 / 20)]
    rms = float(np.sqrt(np.mean(aktivni ** 2))) if aktivni.size else 0.0
    return rms if rms > 10 ** (TICHO_DBFS / 20) else 0.0


def _uroven_vety(v: Veta, hlas: np.ndarray, vad: np.ndarray | None) -> float:
    """Hlasitost původního hlasu ve větě: rámce slov, kde VAD slyší řeč a hlasová
    stopa je úzká. Široký jásot, který separace nechala v hlasové stopě, se nepočítá."""
    r = int(RAMEC_UROVNE_S * SR_MIX)
    usek = hlas[int(v.start * SR_MIX):int(v.end * SR_MIX)]
    n = len(usek) // r
    if n == 0:
        return 0.0
    levy, pravy = usek[:n * r, 0].reshape(n, r), usek[:n * r, 1].reshape(n, r)
    e_mid = (((levy + pravy) / 2) ** 2).mean(axis=1) + 1e-12
    e_side = (((levy - pravy) / 2) ** 2).mean(axis=1)
    ve_slovech = np.zeros(n, dtype=bool)
    for _, od, do in (v.slova or [["", v.start, v.end]]):
        ve_slovech[max(0, int((od - v.start) / RAMEC_UROVNE_S)):
                   min(n, int(np.ceil((do - v.start) / RAMEC_UROVNE_S)))] = True
    if vad is not None:
        i = ((v.start + (np.arange(n) + 0.5) * RAMEC_UROVNE_S) / maska.KROK_VAD_S).astype(int)
        rec = vad[np.minimum(i, len(vad) - 1)] >= VAD_PRAH if len(vad) else np.ones(n, bool)
    else:
        rec = np.ones(n, dtype=bool)
    uzke = e_side < e_mid * 10 ** (UZKY_HLAS_DB / 10)   # u mono (L == R) projde všechno
    for vyber in (ve_slovech & rec & uzke, ve_slovech & rec, ve_slovech):
        if vyber.sum() >= 10:
            return _aktivni_rms(np.sqrt(e_mid[vyber]))
    return _aktivni_rms(np.sqrt(e_mid[ve_slovech]))


def _cilove_urovne(vety: list[Veta], hlas: np.ndarray,
                   vad: np.ndarray | None) -> dict[int, tuple[float, bool]]:
    """Cílová hlasitost (RMS, omezeno?) každé věty, držená u typické hlasitosti mluvčího.
    Věty s tichou nebo neměřitelnou hlasovou stopou v seznamu chybí (dostanou typický zisk)."""
    urovne = {v.id: _uroven_vety(v, hlas, vad) for v in vety}
    vysledek: dict[int, tuple[float, bool]] = {}
    for mluvci in {v.mluvci for v in vety}:
        db = {v.id: 20 * np.log10(urovne[v.id]) for v in vety
              if v.mluvci == mluvci and urovne[v.id] > 0}
        if not db:
            continue
        horni = float(np.percentile(list(db.values()), 90))
        platne = {i: x for i, x in db.items() if x >= horni - MAX_POD_NEJHLASITEJSI_DB}
        median = float(np.median(list(platne.values())))
        for i, x in platne.items():
            omezeno = x
            if len(platne) >= MIN_VET_PRO_SVORKU:
                omezeno = float(np.clip(x, median - MAX_ODCHYLKA_MLUVCIHO_DB,
                                        median + MAX_ODCHYLKA_MLUVCIHO_DB))
            vysledek[i] = (10 ** (omezeno / 20), bool(abs(omezeno - x) > 0.05))
    return vysledek


def _identita_stopa(vety: list[Veta], hlas: np.ndarray, n: int) -> np.ndarray:
    """Test mixu: místo TTS původní hlas v oknech vět. Navazující a překryté věty tvoří
    jeden blok (bez náběhů uvnitř a bez dvojího přičtení); 10ms hrany jen na krajích bloků."""
    okna = np.zeros(n, dtype=np.float32)
    nabeh = int(NABEH_S * SR_MIX)
    rampa = np.linspace(0.0, 1.0, nabeh, dtype=np.float32)
    bloky: list[list[int]] = []
    for a, b in sorted((int(v.start * SR_MIX), min(n, int(v.end * SR_MIX))) for v in vety):
        if a >= b:
            continue
        if bloky and a <= bloky[-1][1] + 2 * nabeh:
            bloky[-1][1] = max(bloky[-1][1], b)
        else:
            bloky.append([a, b])
    for a, b in bloky:
        okna[a:b] = 1.0
        if b - a > 2 * nabeh:
            okna[a:a + nabeh] = rampa
            okna[b - nabeh:b] = rampa[::-1]
    return hlas * okna[:, None]


def _pod_strop(zvuk: np.ndarray, pod: np.ndarray, orig_pk: float) -> tuple[np.ndarray, dict]:
    """Ztiší hlas věty tak, aby s podkladem nepřesáhl strop. Podklad se nemění.

    Strop = −1 dBFS, nebo špička originálu v okně věty (nejvýš −0,5 dBFS). Celá věta se
    ztiší jen podle vzorků, kde podklad sám u stropu není, a nejvýš o 6 dB. Co pak ještě
    přetéká (úder, ořezaná hudba), srovná místní obálka hlasu kolem té špičky, nejvýš
    o dalších 12 dB — česká věta tak nikdy nezmizí; případný zbytek přetečení se zapíše.
    """
    info: dict = {"strop_db": 0.0}
    if not len(zvuk):
        return zvuk, info
    zaklad = min(max(10 ** (STROP_SPICKY_DBFS / 20), orig_pk), STROP_MAX)
    pod_pk = np.abs(pod).max(axis=1)
    volne = pod_pk < zaklad * 10 ** (-1 / 20)

    def spicka(s: float) -> float:
        return float(np.abs(pod[volne] + s * zvuk[volne, None]).max(initial=0.0))

    if spicka(1.0) > zaklad:
        dole, nahore = MIN_ZISK_STROPU, 1.0
        if spicka(dole) <= zaklad:
            for _ in range(14):
                s = (dole + nahore) / 2
                if spicka(s) <= zaklad:
                    dole = s
                else:
                    nahore = s
        zvuk = zvuk * dole
        info["strop_db"] = round(20 * np.log10(dole), 1)

    limit = np.maximum(zaklad, pod_pk)
    if float((np.abs(pod + zvuk[:, None]).max(axis=1) - limit).max()) > 0:
        # největší h ≤ 1, aby |pod_k + h·zvuk| ≤ limit v obou kanálech (znaménko se počítá)
        velikost = np.maximum(np.abs(zvuk), 1e-12)
        souhlasne = np.sign(pod) == np.sign(zvuk)[:, None]
        rezerva = np.where(souhlasne, limit[:, None] - np.abs(pod), limit[:, None] + np.abs(pod))
        h = np.clip(rezerva.min(axis=1) / velikost, 0.0, 1.0).astype(np.float32)
        # rozšířit minimum a vyhladit: výsledek je všude ≤ h, bez lupnutí
        h = minimum_filter1d(h, size=max(1, int(OBALKA_SIRKA_S * SR_MIX)))
        h = np.maximum(uniform_filter1d(h, size=max(1, int(OBALKA_HLADKA_S * SR_MIX))), MIN_OBALKA)
        zvuk = zvuk * h
        info["obalka_db"] = round(20 * np.log10(max(float(h.min()), 1e-6)), 1)
        info["pretekle_vzorky"] = int((np.abs(pod + zvuk[:, None]).max(axis=1) > limit + 1e-6).sum())
    return zvuk, info


def _dabing_stopa(zakazka: Zakazka, vety: list[Veta], urovne: dict[int, tuple[float, bool]],
                  podklad: np.ndarray, spicky: np.ndarray, log: Log) -> tuple[np.ndarray, dict]:
    """Český hlas na časové ose (mono) a záznam hlasitosti vět do qa_mix.json.
    `spicky` = špička originálu po 10ms blocích (podklad už je upravený na místě)."""
    n = len(podklad)
    stopa = np.zeros(n, dtype=np.float32)
    zaznam: dict[str, dict] = {}
    zvuky = {v.id: _nacti(zakazka.slozka / v.tts, SR_MIX, 1) for v in vety}
    potreba: dict[int, float] = {}
    for v in vety:
        cil = urovne.get(v.id, (0.0, False))[0]
        vlastni = _aktivni_rms(_rms_ramcu(zvuky[v.id]))
        if vlastni == 0.0:
            log(f"Pozor: nadabovaný hlas věty {v.id} je skoro tichý — poslechni si ji.")
        if cil > 0 and vlastni > 0:
            potreba[v.id] = 20 * np.log10(cil / vlastni)
    # TTS je soustavně jinak hlasité než originál (typicky ~−8 dB); ±9 dB hlídá jen
    # odchylku jednotlivé věty od typického zisku, ne ten posun. Z méně než 3 vět se
    # typický zisk spolehlivě neurčí (medián dvou = průměr), svorka se pak nepoužije.
    typicky = float(np.median(list(potreba.values()))) if potreba else 0.0
    svorka = MAX_ZISK_VETY_DB if len(potreba) >= MIN_VET_PRO_SVORKU else np.inf
    for v in vety:
        a = int(v.start * SR_MIX)
        zvuk = zvuky.pop(v.id)
        zapis = {"u_mluvciho": urovne.get(v.id, (0.0, False))[1], "strop_db": 0.0}
        if a >= n:
            zaznam[str(v.id)] = {**zapis, "zisk_db": 0.0, "oriznuto_s": round(len(zvuk) / SR_MIX, 2)}
            continue
        chtene = potreba.get(v.id, typicky)
        zisk_db = float(np.clip(chtene, typicky - svorka, typicky + svorka))
        if abs(zisk_db - chtene) > 0.05:
            zapis["zisk_omezen_db"] = round(zisk_db - chtene, 1)
        if abs(zisk_db - chtene) >= 1.0:  # menší omezení není slyšet — jen do qa_mix.json
            log(f"Pozor: hlasitost věty {v.id} je o {abs(zisk_db - chtene):.1f} dB mimo ostatní "
                "— poslechni si ji.")
        zvuk *= 10 ** (zisk_db / 20)
        nabeh = int(NABEH_S * SR_MIX)
        if len(zvuk) > 2 * nabeh:
            okno = np.linspace(0.0, 1.0, nabeh, dtype=np.float32)
            zvuk[:nabeh] *= okno
            zvuk[-nabeh:] *= okno[::-1]
        b = min(n, a + len(zvuk))
        if b - a < len(zvuk):
            zapis["oriznuto_s"] = round((len(zvuk) - (b - a)) / SR_MIX, 2)
            zvuk = zvuk[:b - a]
        zvuk, spicka = _pod_strop(zvuk, podklad[a:b] + stopa[a:b, None],
                                  float(spicky[a // BLOK_SPICKY:-(-b // BLOK_SPICKY)].max(initial=0.0)))
        zapis.update(spicka)
        if spicka.get("strop_db", 0.0) <= -6.0 or spicka.get("obalka_db", 0.0) <= -6.0:
            log(f"Pozor: věta {v.id} se kvůli špičkám v podkladu místy ztišila "
                f"(celá {spicka.get('strop_db', 0.0):.0f} dB, místně {spicka.get('obalka_db', 0.0):.0f} dB).")
        stopa[a:b] += zvuk
        zaznam[str(v.id)] = {**zapis, "zisk_db": round(zisk_db + zapis["strop_db"], 1)}
    return stopa, zaznam


def _dabovane_vety(zakazka: Zakazka, identita: bool) -> list[Veta]:
    if identita:
        # stejná sada vět jako dabing (když už překlad je), ať test prověří tutéž masku
        prelozene = [v for v in zakazka.vety if v.cs.strip()]
        return prelozene or [v for v in zakazka.vety if v.en]
    chybi = []
    for v in zakazka.vety:
        if not v.cs.strip():
            continue
        try:
            if not v.tts or sf.info(str(zakazka.slozka / v.tts)).frames == 0:
                chybi.append(v.id)
        except (OSError, RuntimeError):  # neexistující nebo poškozený soubor
            chybi.append(v.id)
    if chybi:
        raise RuntimeError(f"Chybí nadabovaný hlas u vět {chybi[:10]}{'…' if len(chybi) > 10 else ''} "
                           "— spusť dabing znovu.")
    for v in zakazka.vety:
        v.qa = [q for q in v.qa if q != "bez_dabingu"]
        if not v.cs.strip():
            v.qa.append("bez_dabingu")  # bez překladu: zní originál, hlas se neodečítá
    return [v for v in zakazka.vety if v.cs.strip()]


def _nacti_efekty(zakazka: Zakazka, n: int, log: Log) -> tuple[np.ndarray | None, str]:
    """Stopa efektů z BandItu nad hlasovou stopou Demucsu (jásot, potlesk, smích), 48 kHz."""
    cesta, info, hlas = (zakazka.cesta("efekty_hlas.wav"), zakazka.cesta("dialog_hlas.json"),
                         zakazka.cesta("vocals.wav"))
    if not cesta.exists() or not info.exists():
        log("BandIt neběžel — jásot a smích přes řeč zmizí spolu s hlasem.")
        return None, "jen_demucs"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
        hotove = data.get("stopy") or []
        model_ok = (data.get("model") == MODEL_DIALOG_CHECKPOINT.name
                    and data.get("verze") == DIALOG_VERZE)
    except (ValueError, AttributeError, OSError):
        hotove, model_ok = [], False
    if "effects" not in hotove or not model_ok:
        log("Pozor: stopa efektů z BandItu chybí nebo je z jiné verze modelu — nepoužívám ji.")
        return None, "jina_verze_efektu"
    if cesta.stat().st_mtime < hlas.stat().st_mtime:
        log("Pozor: stopa efektů z BandItu je starší než hlasová stopa — nepoužívám ji.")
        return None, "zastarale_efekty"
    efekty = _nacti(cesta, SR_MIX, 2)
    if len(efekty) != n:
        efekty = np.pad(efekty[:n], ((0, max(0, n - len(efekty))), (0, 0)))
    return efekty, "demucs_bez_efektu_bandit"


def _nacti_vad(zakazka: Zakazka, delka_s: float, log: Log) -> tuple[np.ndarray | None, str]:
    cesta, hlas = zakazka.cesta("vad.npy"), zakazka.cesta("vocals16.wav")
    if not cesta.exists():
        log("Pozor: chybí detekce řeči (vad.npy) — podklad se řídí jen časy slov.")
        return None, "chybi"
    if hlas.exists() and cesta.stat().st_mtime < hlas.stat().st_mtime:
        log("Pozor: detekce řeči je starší než hlasová stopa — nepoužívám ji, podklad jen podle slov.")
        return None, "zastarala"
    try:
        vad = np.load(cesta).astype(np.float32)
    except (ValueError, OSError, EOFError):
        log("Pozor: detekce řeči (vad.npy) je poškozená — podklad se řídí jen časy slov.")
        return None, "poskozena"
    if len(vad) * maska.KROK_VAD_S < delka_s - 1.0:
        log("Pozor: detekce řeči nepokrývá celé video — konec se řídí jen časy slov.")
        return vad, "kratka"
    return vad, "ok"


def _loudnorm_mereni(cesta: Path) -> dict:
    vystup = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(cesta), "-af",
                             "loudnorm=print_format=json", "-f", "null", "-"],
                            capture_output=True, text=True)
    nalez = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", vystup.stderr, flags=re.S)
    if not nalez:
        raise RuntimeError("loudnorm nevrátil měření:\n" + vystup.stderr[-500:])
    return json.loads(nalez[-1])


def _cislo(x) -> float | None:
    """Měření loudnorm; u ticha vrací -inf, to do JSON nepatří."""
    x = float(x)
    return round(x, 2) if math.isfinite(x) else None


def _zapis(cesta: Path, x: np.ndarray, format_: str, subtype: str) -> None:
    """Zápis do celočíselného formátu s ořezem (libsndfile by přetečení nepřesekl)."""
    sf.write(cesta, np.clip(x, -1.0, 1.0), SR_MIX, format=format_, subtype=subtype)


def sloz(zakazka: Zakazka, log: Log, identita: bool = False) -> dict[str, Path]:
    """Složí mix a vyexportuje MP4 (je-li video), WAV, podklad a SRT. Vrací cesty výstupů."""
    log("Skládám hlas a pozadí…")
    vety = _dabovane_vety(zakazka, identita)
    original = _nacti(zakazka.cesta("audio_hq.wav"), SR_MIX, 2)
    hlas = _nacti(zakazka.cesta("vocals.wav"), SR_MIX, 2)
    n = len(original)
    if len(hlas) != n:
        log(f"Pozor: hlasová stopa má {len(hlas) / SR_MIX:.2f} s, originál {n / SR_MIX:.2f} s — srovnávám.")
        hlas = np.pad(hlas[:n], ((0, max(0, n - len(hlas))), (0, 0)))

    vad, stav_vad = _nacti_vad(zakazka, n / SR_MIX, log)
    kontext = maska.Kontext.z_hlasu(hlas.mean(axis=1), SR_MIX, vad)
    useky = maska.useky_aktivity(vety, kontext)
    # věty bez překladu mají znít v originále, i když k nim sahá rezerva sousední věty
    dabovane = {v.id for v in vety}
    chranene = [(v.start, v.end) for v in zakazka.vety if v.id not in dabovane]
    g = maska.maska(useky, kontext, SR_MIX, n, chranene)
    pokryti = float((g > 0).mean()) * 100
    log(f"Hlas mluvčího se odečítá v {pokryti:.0f} % času, jinde hraje originál beze změny.")
    # odhad hlasu mluvčího = Demucs vocals bez toho, co BandIt v nich označil jako efekty;
    # stopa hudby z BandItu se nepoužívá (plete si s ní kusy řeči)
    efekty, odhad_hlasu = _nacti_efekty(zakazka, n, log)
    efekty_db = None
    if efekty is not None:
        v_masce = g > 0.5
        if v_masce.any():  # kontrola do qa: efekty mají být jen malá část hlasové stopy
            efekty_db = round(10 * np.log10(float(np.mean(efekty[v_masce] ** 2)) /
                                            (float(np.mean(hlas[v_masce] ** 2)) + 1e-20) + 1e-20), 1)
        hlas -= efekty
        del efekty
        log("Jásot, potlesk a smích z hlasové stopy vracím do pozadí (BandIt).")
    if identita:
        stopa = _identita_stopa(vety, hlas, n)
    else:
        urovne = _cilove_urovne(vety, hlas, vad)
        spicky = np.abs(original[:n - n % BLOK_SPICKY]).reshape(-1, BLOK_SPICKY * 2).max(axis=1)
        if n % BLOK_SPICKY:
            spicky = np.append(spicky, np.abs(original[n - n % BLOK_SPICKY:]).max())

    podklad = original                      # na místě: podklad = originál − g·hlas
    hlas *= g[:, None]
    podklad -= hlas
    del hlas, g

    vystupy = zakazka.cesta("vystup")
    vystupy.mkdir(exist_ok=True)
    jmeno = bezpecne_jmeno(zakazka.nazev) + (".identita" if identita else f".{zakazka.jazyk}")
    soubory: dict[str, Path] = {}
    qa: dict = {"pokryti_%": round(pokryti, 1), "useku": len(useky), "vad": stav_vad,
                "odhad_hlasu": odhad_hlasu, "efekty_vs_hlas_db": efekty_db}
    if identita:
        podklad += stopa
        del stopa
    else:
        stopa, qa["vety"] = _dabing_stopa(zakazka, vety, urovne, podklad, spicky, log)
        # podklad dabingu = originál bez dabovaného mluvčího (nepřeložená řeč v něm zůstává)
        soubory["podklad"] = vystupy / f"{bezpecne_jmeno(zakazka.nazev)}.podklad.flac"
        (vystupy / f"{bezpecne_jmeno(zakazka.nazev)}.pozadi.flac").unlink(missing_ok=True)  # starší název
        _zapis(soubory["podklad"], podklad, "FLAC", "PCM_24")
        podklad += stopa[:, None]
        del stopa
        omezeno = sum(z["u_mluvciho"] for z in qa["vety"].values())
        strop = sum(z["strop_db"] < 0 or z.get("obalka_db", 0.0) < 0 for z in qa["vety"].values())
        oriznuto = [k for k, z in qa["vety"].items() if z.get("oriznuto_s")]
        if omezeno or strop:
            log(f"Hlasitost vět: {omezeno}× držena u typické hlasitosti mluvčího, "
                f"{strop}× ztišena kvůli špičce.")
        if oriznuto:
            log(f"Pozor: hlas vět {oriznuto} přesahuje konec videa a je useknutý.")
    hotovy = zakazka.cesta("mix.identita.wav" if identita else "mix.wav")
    sf.write(hotovy, podklad, SR_MIX, subtype="FLOAT")
    del podklad

    log("Měřím hlasitost…")
    orig, vysl = _loudnorm_mereni(zakazka.cesta("audio_hq.wav")), _loudnorm_mereni(hotovy)
    mereni = {"original_lufs": _cislo(orig["input_i"]), "mix_lufs": _cislo(vysl["input_i"]),
              "original_tp": _cislo(orig["input_tp"]), "mix_tp": _cislo(vysl["input_tp"])}
    qa.update(mereni)
    if not identita:
        zakazka.codex["mix"] = mereni
    if mereni["mix_lufs"] is not None and mereni["original_lufs"] is not None:
        rozdil = mereni["mix_lufs"] - mereni["original_lufs"]
        if abs(rozdil) > 1.0:
            log(f"Pozor: dabing je o {rozdil:+.1f} LU {'hlasitější' if rozdil > 0 else 'tišší'} "
                "než originál.")
    zakazka.cesta("qa_mix.identita.json" if identita else "qa_mix.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=1), encoding="utf-8")

    wav = vystupy / f"{jmeno}.wav"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(hotovy),
                    "-c:a", "pcm_s24le", str(wav)], check=True)
    soubory["wav"] = wav
    zdroj = zakazka.cesta("source.mp4")
    if zdroj.exists() and ma_video(zdroj):
        log("Exportuji video…")
        soubory["mp4"] = export_mp4(zdroj, hotovy, vystupy / f"{jmeno}.mp4", zakazka.jazyk, log)
    if not identita:
        soubory["srt"] = srt(zakazka.vety, vystupy / f"{jmeno}.srt", n / SR_MIX)
    zakazka.hotovo("identita" if identita else "export")
    return soubory
