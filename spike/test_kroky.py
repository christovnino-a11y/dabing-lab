"""Ruční průchod pipeline po krocích (pro ladění). Použití:
   .venv/bin/python spike/test_kroky.py <soubor|odkaz> <krok…>
   kroky: priprav analyzuj identita preloz nadabuj
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dabing.pipeline import Pipeline, test_mixu

zdroj, kroky = sys.argv[1], sys.argv[2:]
p = Pipeline(lambda r: print(r, flush=True), lambda x, z: print(f"  [{x*100:5.1f} %] {z}", flush=True))
t = time.monotonic()
z = p.priprav(zdroj, "cs", "codex", None)
for k in kroky:
    t0 = time.monotonic()
    if k == "analyzuj": p.analyzuj(z)
    elif k == "identita": print(test_mixu(z.id))
    elif k == "preloz": p.preloz(z)
    elif k == "nadabuj": print(p.nadabuj(z))
    print(f"== {k} hotovo za {time.monotonic()-t0:.1f} s", flush=True)
print(f"celkem {time.monotonic()-t:.1f} s, zakázka {z.id}")
