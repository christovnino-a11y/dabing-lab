"""Stažení, převody a export — jen yt-dlp a ffmpeg, žádné modely."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .jazyky import DABING
from .job import Veta

Log = Callable[[str], None]

YT_DLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

# H.264 do 1080p: přehraje ho QuickTime i Finder a video se při exportu jen kopíruje.
FORMAT_VIDEA = "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b"

ISO3 = {kod: j.iso3 for kod, j in DABING.items()}
NAZVY_STOP = {kod: j.stopa for kod, j in DABING.items()}


def spust(prikaz: list[str], log: Log | None = None, cwd: Path | None = None) -> None:
    """Spustí příkaz, řádky výstupu posílá do logu. Stejná skupina procesů jako lab."""
    proces = subprocess.Popen(prikaz, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, cwd=cwd, bufsize=1)
    posledni: list[str] = []
    assert proces.stdout is not None
    for radek in proces.stdout:
        radek = radek.rstrip()
        if radek:
            posledni = (posledni + [radek])[-20:]
            if log:
                log(radek)
    if proces.wait() != 0:
        raise RuntimeError(f"{Path(prikaz[0]).name} skončil chybou:\n" + "\n".join(posledni[-8:]))


def info(url: str) -> dict:
    """ID, název, délka, popis a nabídka titulků videa bez stahování."""
    vystup = subprocess.run([YT_DLP, "-J", "--no-playlist", "--no-warnings", url],
                            capture_output=True, text=True, timeout=120)
    if vystup.returncode != 0:
        raise RuntimeError("yt-dlp nenačetl video:\n" + vystup.stderr.strip()[-600:])
    data = json.loads(vystup.stdout)
    return {"id": data["id"], "nazev": data.get("title") or data["id"],
            "delka_s": float(data.get("duration") or 0.0),
            "popis": (data.get("description") or "").strip(),
            "jazyk": _jazyk(data.get("language") or ""),
            "rucni": sorted(k for k in (data.get("subtitles") or {}) if k != "live_chat")}


def _jazyk(kod: str) -> str:
    """„en-US" / „nl-NL-orig" → „en" / „nl"."""
    return kod.split("-")[0].lower()


def _vyber_titulky(info: dict, jazyk: str) -> str | None:
    """Ruční titulky od autora v jazyce originálu. Automatické titulky YouTube se nepoužívají:
    v testu 2026-09-28 (Codex, Meta Connect) s nimi překlad převzal jejich věrohodně vypadající
    chyby („Aviator" místo Adventurer, „$12.99" místo $1,299), zatímco nesmysly našeho přepisu
    Codex opraví podle smyslu sám. Potvrzeno slepým testem celého videa (2 × 2 překlady, 2 hodnotitelé
    ve shodě): s automatickými titulky 8 vážných chyb, bez nich 4."""
    rucni = [k for k in info.get("rucni", []) if _jazyk(k) == jazyk]
    return rucni[0] if rucni else None


ZNACKY_TITULKU = re.compile(r"\[[^\]]*\]|\([^)]*\)|>>|♪")


def _slova_json3(data: dict) -> list[list]:
    """Titulky YouTube (json3) → [[slovo, začátek_s], …] bez značek řečníka (>>), zvuků
    ([applause], (Laughter), (Audience)) a not (♪) — dabér by je jinak přeložil a vyslovil.
    Automatické titulky mají čas každého slova; ruční jen řádku — slova se po ní rozprostřou."""
    slova: list[list] = []
    for udalost in data.get("events", []):
        start, delka = udalost.get("tStartMs", 0), udalost.get("dDurationMs", 0)
        segmenty = [(seg.get("tOffsetMs"), ZNACKY_TITULKU.sub(" ", seg.get("utf8", "")).split())
                    for seg in udalost.get("segs") or []]
        # ruční titulky mívají v řádku víc segmentů (kurzíva, barva) — rozprostřít je přes událost
        # jedním společným indexem, ne každý segment znovu od tStartMs
        bez_casu = sum(len(casti) for posun, casti in segmenty if posun is None)
        k = 0
        for posun, casti in segmenty:
            for slovo in casti:
                if posun is None:
                    t = start + delka * k / bez_casu
                    k += 1
                else:
                    t = start + posun
                slova.append([slovo, round(t / 1000, 2)])
    return slova


def stahni_titulky(url: str, slozka: Path, info: dict, jazyk: str) -> dict | None:
    """Ruční titulky od autora v jazyce originálu → titulky.json {"stopa", "druh", "jazyk", "slova"}.
    Jen text (--skip-download). None, když je video v jazyce originálu nemá."""
    if jazyk == "auto":
        jazyk = info.get("jazyk", "")
        if not jazyk:  # yt-dlp jazyk videa nezná vždy — pak ho prozradí jazyk ručních titulků
            zakladni = {_jazyk(k) for k in info.get("rucni", [])}
            jazyk = zakladni.pop() if len(zakladni) == 1 else ""
    cil = slozka / "titulky.json"
    if cil.exists():
        ulozene = json.loads(cil.read_text(encoding="utf-8"))
        # neznámý jazyk (yt-dlp tentokrát titulky ani jazyk nevrátil) není důvod titulky zahodit
        if not jazyk or ulozene.get("jazyk") == jazyk:
            return ulozene
        cil.unlink()  # titulky jiného jazyka originálu by překladač mátly
    stopa = _vyber_titulky(info, jazyk) if jazyk else None
    if stopa is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        vystup = subprocess.run([YT_DLP, "--no-playlist", "--no-warnings", "--skip-download",
                                 "--write-subs", "--sub-langs", stopa, "--sub-format", "json3",
                                 "-o", str(Path(tmp) / "t.%(ext)s"), url],
                                capture_output=True, text=True, timeout=180)
        soubor = next(Path(tmp).glob("t.*json3"), None)
        if vystup.returncode != 0 or soubor is None:
            raise RuntimeError((vystup.stderr.strip() or "yt-dlp titulky nevrátil")[-300:])
        slova = _slova_json3(json.loads(soubor.read_text(encoding="utf-8")))
    data = {"stopa": stopa, "druh": "rucni", "jazyk": jazyk, "slova": slova}
    docasny = cil.with_suffix(".tmp")
    docasny.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(docasny, cil)
    return data


def stahni(url: str, slozka: Path, log: Log | None = None) -> Path:
    cil = slozka / "source.mp4"
    if cil.exists() and cil.stat().st_size > 0:
        return cil
    spust([YT_DLP, "--no-playlist", "--newline", "--no-warnings", "-f", FORMAT_VIDEA,
           "--merge-output-format", "mp4", "-o", str(slozka / "source.%(ext)s"), url], log)
    if not cil.exists():
        kandidati = sorted(slozka.glob("source.*"))
        raise RuntimeError(f"Po stažení chybí source.mp4 (našel jsem {[k.name for k in kandidati]})")
    return cil


def delka(cesta: Path) -> float:
    vystup = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(cesta)],
                            capture_output=True, text=True, check=True)
    return float(vystup.stdout.strip())


def ma_video(cesta: Path) -> bool:
    vystup = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=codec_type", "-of", "csv=p=0", str(cesta)],
                            capture_output=True, text=True)
    return "video" in vystup.stdout


def extrahuj_zvuk(zdroj: Path, cil: Path, sr: int, kanaly: int) -> Path:
    if cil.exists() and cil.stat().st_size > 0:
        return cil
    spust([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(zdroj), "-vn",
           "-ac", str(kanaly), "-ar", str(sr), "-c:a", "pcm_f32le", str(cil)])
    return cil


# původní zvuk jde do MP4 kopií jen v kodecích, které přehraje i QuickTime
KOPIROVATELNE = {"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac"}


def kodek_zvuku(cesta: Path) -> str:
    vystup = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                             "stream=codec_name", "-of", "csv=p=0", str(cesta)],
                            capture_output=True, text=True)
    return vystup.stdout.strip().split(",")[0]  # u AC-3 přidá ffprobe prázdné pole


def export_mp4(video: Path, mix: Path, cil: Path, jazyk: str, log: Log | None = None) -> Path:
    """Video i původní zvuk se kopírují, dabing je výchozí stopa, originál zůstává jako druhá."""
    kod = ISO3.get(jazyk, "und")
    original = (["-c:a:1", "copy"] if kodek_zvuku(video) in KOPIROVATELNE
                else ["-c:a:1", "aac", "-b:a:1", "256k"])
    spust([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(video), "-i", str(mix),
           "-map", "0:v:0", "-map", "1:a:0", "-map", "0:a:0",
           "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "256k", *original,
           "-metadata:s:a:0", f"language={kod}",
           "-metadata:s:a:0", f"title={NAZVY_STOP.get(jazyk, 'Dabing')}",
           "-metadata:s:a:1", "title=Originál",
           "-disposition:a:0", "default", "-disposition:a:1", "0",
           "-movflags", "+faststart", str(cil)], log)
    return cil


def _cas_srt(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sek, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sek:02d},{ms:03d}"


def srt(vety: list[Veta], cil: Path, delka_s: float | None = None) -> Path:
    """Titulky českého dabingu; nic za koncem zvuku."""
    bloky = []
    konec_zvuku = delka_s if delka_s else float("inf")
    for i, v in enumerate((v for v in vety if v.cs.strip() and v.start < konec_zvuku), 1):
        konec = min(v.start + (v.vysledna_s or v.delka), konec_zvuku)
        bloky.append(f"{i}\n{_cas_srt(v.start)} --> {_cas_srt(konec)}\n{v.cs.strip()}\n")
    cil.write_text("\n".join(bloky), encoding="utf-8")
    return cil
