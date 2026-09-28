"""Spike A: umí Codex app-server přepsat, rozlišit mluvčí a přeložit zvuk?

Měří: modely, limit velikosti audia, víc audio položek v jednom turnu,
přesnost přepisu, mluvčí, překlad s rozpočtem délky, pokyn k přednesu,
tokeny a kvótu před a po. Výsledky do spike/out/spike_a.json.

Spuštění: .venv/bin/python spike/spike_codex_audio.py [--jen-limit]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB))

from dabing.codex_client import Codex, audio, text  # noqa: E402

OUT = LAB / "spike" / "out"
ZNAKU_ZA_SEKUNDU_CS = 13.0  # kalibrace češtiny z VoiceStudia (duration_planner)


def promluvy(wav: Path, pauza_s: float = 0.35, min_s: float = 1.0, max_s: float = 12.0):
    """Hrubý energetický detektor řeči — jen pro spike (produkce: dabing/vad.py)."""
    x, sr = sf.read(wav, dtype="float32")
    ramec = int(0.03 * sr)
    n = len(x) // ramec
    e = np.sqrt((x[: n * ramec].reshape(n, ramec) ** 2).mean(axis=1) + 1e-12)
    prah = max(np.percentile(e, 20) * 3.0, 0.004)
    rec = e > prah
    kusy, zacatek, ticho = [], None, 0
    for i, r in enumerate(rec):
        if r:
            if zacatek is None:
                zacatek = i
            ticho = 0
        elif zacatek is not None:
            ticho += 1
            delka = (i - zacatek) * 0.03
            if ticho * 0.03 >= pauza_s or delka >= max_s:
                konec = i - ticho + 1
                if (konec - zacatek) * 0.03 >= min_s:
                    kusy.append((zacatek * 0.03, konec * 0.03))
                zacatek, ticho = None, 0
    if zacatek is not None and (n - zacatek) * 0.03 >= min_s:
        kusy.append((zacatek * 0.03, n * 0.03))
    return kusy, x, sr


def uloz_ogg(x: np.ndarray, sr: int, od: float, do: float, cil: Path) -> Path:
    wav = cil.with_suffix(".wav")
    sf.write(wav, x[int(od * sr) : int(do * sr)], sr)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
         "-c:a", "libopus", "-b:a", "24k", "-ac", "1", str(cil)],
        check=True,
    )
    wav.unlink()
    return cil


SCHEMA = {
    "type": "object",
    "properties": {
        "mluvci": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "pohlavi": {"type": "string", "enum": ["muz", "zena", "dite", "nejasne"]},
                    "popis": {"type": "string"},
                },
                "required": ["id", "pohlavi", "popis"],
                "additionalProperties": False,
            },
        },
        "vety": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "mluvci": {"type": "string"},
                    "en": {"type": "string"},
                    "cs": {"type": "string"},
                    "prednes": {"type": "string"},
                },
                "required": ["id", "mluvci", "en", "cs", "prednes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["mluvci", "vety"],
    "additionalProperties": False,
}

POKYNY = """Jsi dabingový editor. Dostaneš očíslované promluvy z anglického videa (jen hlas, bez hudby).
Pro každou promluvu:
1. `en`: přesný anglický přepis toho, co zazní (nic nepřidávej ani nevynechávej).
2. `mluvci`: označ mluvčího písmenem (A, B, C…). Stejný hlas = stejné písmeno napříč promluvami.
3. `cs`: přirozená mluvená čeština pro dabing. Musí se vejít do uvedeného limitu znaků
   (odpovídá délce promluvy). Raději zkrať obsah, než abys limit překročil. Zachovej význam, jména a čísla.
4. `prednes`: jedno až tři slova česky, jak to mluvčí říká (např. „nadšeně", „klidně", „vážně", „se smíchem").
V `mluvci` popiš každého mluvčího (pohlaví, hlas, role).
Nepoužívej žádné nástroje, příkazy ani soubory — odpovídej jen podle poslechu přiložených zvuků."""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    zaznam: dict = {"cas": time.strftime("%Y-%m-%d %H:%M:%S")}
    with Codex() as codex:
        zaznam["modely"] = codex.modely()
        zaznam["limity_pred"] = codex.limity()
        print("modely:", zaznam["modely"])

        # 1) limit velikosti: 5 min WAV (9,6 MB) — jen s --limit (už změřeno: prošlo)
        velky = OUT / "vocals300_16k.wav"
        if "--limit" in sys.argv or "--jen-limit" in sys.argv:
          r = codex.turn([text("Kolik slov zhruba zazní? Odpověz jen číslem."), audio(velky)], effort="low")
          zaznam["limit_test"] = {"bajtu": velky.stat().st_size, "status": r.status, "chyba": r.chyba,
                                "text": r.text[:200], "sekundy": round(r.sekundy, 1), "tokeny": r.tokeny}
          print("limit:", zaznam["limit_test"], r.typy_polozek)
        if "--jen-limit" in sys.argv:
            (OUT / "spike_a.json").write_text(json.dumps(zaznam, ensure_ascii=False, indent=2))
            return

        # 2) přepis + mluvčí + překlad: očíslované promluvy z 60 s
        kusy, x, sr = promluvy(OUT / "vocals60_16k.wav")
        polozky = [text(POKYNY)]
        seznam = []
        for i, (od, do) in enumerate(kusy, 1):
            soubor = uloz_ogg(x, sr, od, do, OUT / f"promluva_{i:02d}.ogg")
            limit = int((do - od) * ZNAKU_ZA_SEKUNDU_CS)
            polozky.append(text(f"Promluva #{i} ({do - od:.1f} s, limit {limit} znaků):"))
            polozky.append(audio(soubor))
            seznam.append({"id": i, "od": round(od, 2), "do": round(do, 2), "limit": limit,
                           "bajtu": soubor.stat().st_size})
        zaznam["promluvy"] = seznam
        print(f"{len(kusy)} promluv, celkem {sum(s['bajtu'] for s in seznam)} B")

        r = codex.turn(polozky, schema=SCHEMA)
        zaznam["preklad"] = {"status": r.status, "chyba": r.chyba, "sekundy": round(r.sekundy, 1),
                             "tokeny": r.tokeny, "typy_polozek": r.typy_polozek}
        try:
            zaznam["preklad"]["vysledek"] = r.json()
        except Exception:  # noqa: BLE001
            zaznam["preklad"]["text"] = r.text
        zaznam["limity_po"] = codex.limity()
        if r.chyba:
            zaznam["stderr"] = codex.stderr()[-30:]
    (OUT / "spike_a.json").write_text(json.dumps(zaznam, ensure_ascii=False, indent=2))
    print("hotovo →", OUT / "spike_a.json")


if __name__ == "__main__":
    main()
