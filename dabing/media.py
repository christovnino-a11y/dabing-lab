"""Stažení, převody a export — jen yt-dlp a ffmpeg, žádné modely."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from .job import Veta

Log = Callable[[str], None]

YT_DLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

# H.264 do 1080p: přehraje ho QuickTime i Finder a video se při exportu jen kopíruje.
FORMAT_VIDEA = "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b"

ISO3 = {"cs": "ces", "sk": "slk", "de": "deu", "en": "eng"}
NAZVY_STOP = {"cs": "Čeština (dabing)", "sk": "Slovenčina (dabing)", "de": "Deutsch (Synchron)",
              "en": "English (dub)"}


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
    """ID, název a délka videa bez stahování."""
    vystup = subprocess.run([YT_DLP, "-J", "--no-playlist", "--no-warnings", url],
                            capture_output=True, text=True, timeout=120)
    if vystup.returncode != 0:
        raise RuntimeError("yt-dlp nenačetl video:\n" + vystup.stderr.strip()[-600:])
    data = json.loads(vystup.stdout)
    return {"id": data["id"], "nazev": data.get("title") or data["id"],
            "delka_s": float(data.get("duration") or 0.0)}


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
    return vystup.stdout.strip()


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


def srt(vety: list[Veta], cil: Path) -> Path:
    bloky = []
    for i, v in enumerate((v for v in vety if v.cs.strip()), 1):
        konec = v.start + (v.vysledna_s or v.delka)
        bloky.append(f"{i}\n{_cas_srt(v.start)} --> {_cas_srt(konec)}\n{v.cs.strip()}\n")
    cil.write_text("\n".join(bloky), encoding="utf-8")
    return cil
