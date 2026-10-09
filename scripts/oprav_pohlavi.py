"""Oprava rodu a smíchaných mluvčích v už hotové zakázce, bez nového stahování a přepisu.

Změří pohlaví mluvčích (a rozdělí mluvčího, ve kterém diarizace slila muže a ženu), přeloží znovu
jen věty, kterých se změna týká, a nadabuje jen věty se změněným textem nebo hlasem (hlas
nezměněných vět se bere z mezipaměti). Zakázku nejdřív zazálohuje (job.json a hotové MP4 + SRT
do složky `zaloha-pred-pohlavim-<datum>` v zakázce).

Použití (z kořene repozitáře):
    .venv/bin/python scripts/oprav_pohlavi.py <id_zakazky> [--snimky]

--snimky  u mluvčího, jehož pohlaví nejde určit z hlasu, smí Codex dostat 3 snímky videa
          (jediný případ, kdy z Macu odchází obraz). Bez přepínače se nic neposílá.
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dabing.job import Zakazka  # noqa: E402
from dabing.pipeline import Pipeline  # noqa: E402

if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
    raise SystemExit(__doc__)
ID = sys.argv[1]
SNIMKY = "--snimky" in sys.argv
posledni = [0.0, ""]


def log(zprava: str) -> None:
    print(time.strftime("%H:%M:%S ") + zprava, flush=True)


def prubeh(p: float, zprava: str) -> None:
    if zprava.split(" (")[0] != posledni[1].split(" (")[0] or p - posledni[0] >= 0.02:
        print(time.strftime("%H:%M:%S ") + f"  {p:.0%} {zprava}", flush=True)
        posledni[:] = [p, zprava]


z = Zakazka.nacti(ID)
zaloha = z.cesta("zaloha-pred-pohlavim-" + time.strftime("%Y-%m-%d"))
if not zaloha.exists():
    zaloha.mkdir()
    shutil.copy2(z.cesta("job.json"), zaloha / "job.json")
    for f in [*z.cesta("vystup").glob("*.mp4"), *z.cesta("vystup").glob("*.srt")]:
        shutil.copy2(f, zaloha / f.name)
    log(f"Záloha: {zaloha}")
if SNIMKY:
    z.snimky_pohlavi = True
puvodni = {v.id: v.cs for v in z.vety}
p = Pipeline(log, prubeh)
p._pohlavi(z)
p.kotvy(z)
p.preloz(z)
log(f"Změněný překlad: {sum(1 for v in z.vety if v.cs != puvodni[v.id])} z {len(z.vety)} vět")
soubory = p.nadabuj(z)
print("HOTOVO", {k: str(v) for k, v in soubory.items()}, flush=True)
