"""Testy detekce zkomolených slov (dabing/qa_text.py) — čistý Python, bez modelů.

Spuštění: .venv/bin/python tests/test_qa_text.py   (nebo pytest, pokud je nainstalovaný)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dabing.qa_text import cer, nacti_qa, oznac_zkomolena, slovnik_jmen, zkomolena_slova  # noqa: E402

ZAKAZKA = Path(__file__).resolve().parent.parent / "jobs" / "PvFjbPYEOZI"


def test_zkomolene_slovo_v_dlouhe_vete():
    ref = "Od výpočtů na gé pé jú přes Veru Rubin, naši úplně nejnovější generaci,"
    hyp = "Od výpočtu na GP-u přes veru Rubin naši úplně nejnovější genesraci."
    assert zkomolena_slova(ref, hyp) == ["generaci→genesraci"]
    assert cer(ref, hyp) < 0.3  # právě proto se větná CER nestačí


def test_vypustena_slabika():
    ref = "Jak jsem zmínila, dosahujeme s každou generací výkonu daleko za hranicí rozumu."
    hyp = "Jak jsem zmínila, dosahujeme s každou genací výkonu daleko za hranicí rozumu."
    assert zkomolena_slova(ref, hyp) == ["generací→genací"]
    ref = "Ale zároveň přecházíme k další generaci."
    hyp = "Ale zároveň přezíme k další generaci."
    assert zkomolena_slova(ref, hyp) == ["přecházíme→přezíme"]


def test_dobra_veta_nic_nehlasi():
    t = "Dnes ukážeme, jak funguje nová generace čipů."
    assert zkomolena_slova(t, t) == []
    assert zkomolena_slova(t, t.lower().replace(",", "")) == []


def test_jmena_a_cizi_pravopis_se_nepocitaji():
    # vlastní jméno uprostřed věty (ref) a jméno napsané velkým písmenem (přepis)
    assert zkomolena_slova("Je to jazyk Pascal pro studenty.", "Je to jazyk Paskal pro studenty.") == []
    assert zkomolena_slova("Používá se to na gé pé jú dnes.", "Používá se to na GP-u dnes.") == []
    # ASR napsal cizí slovo po svém, hlas ho řekl dobře
    assert zkomolena_slova("Zapni gigawattový hardwaru teď.", "Zapni gigavattový hardvaru teď.") == []
    assert zkomolena_slova("Stavíme sendboks pro agenty.", "Stavíme sandbox pro agenty.") == []
    # číslice místo slova
    assert zkomolena_slova("Je to desetkrát rychlejší.", "Je to 10krát rychlejší.") == []


def test_jiny_tvar_a_jine_slovo_a_slepeni():
    assert zkomolena_slova("Tato generace je rychlá.", "Tato generaci je rychlá.") == []  # jen koncovka
    assert zkomolena_slova("Vidíme kamion před domem.", "Vidíme zítra před domem.") == []  # jiné slovo
    assert zkomolena_slova("Máme zároveň dvě možnosti.", "Máme zároveň dvěmožnosti.") == []  # slepení
    assert zkomolena_slova("Máme několik možností.", "Máme ne několik možností.") == []  # vložené slovo


def test_kratka_slova_se_neresi():
    assert zkomolena_slova("Je to tam teď.", "Je to tám def.") == []


def test_jmeno_na_zacatku_vety_podle_slovniku():
    texty = ["Dnes mluví šéf Nvidia o čipech.", "Envidia nabízí celý software."]
    jmena = slovnik_jmen(texty)
    assert zkomolena_slova(texty[1], "Nvidia nabízí celý software.", jmena) == []
    assert zkomolena_slova("Generace přichází.", "Genesrace přichází.", jmena) == ["generace→genesrace"]


def test_oznac_a_nacti_qa(tmp_path=None):
    import tempfile
    slozka = Path(tmp_path or tempfile.mkdtemp())
    data = {"1": {"cer": 0.1, "prepis": "Naši nejnovější genesraci."}, "2": {"cer": 0.0, "prepis": "Ahoj."}}
    cesta = slozka / "qa_zpet.json"
    cesta.write_text(json.dumps(data), encoding="utf-8")

    class V:  # jako dabing.job.Veta
        def __init__(self, id, cs, vyslov=""):
            self.id, self.cs, self.vyslov = id, cs, vyslov

    nacteno = nacti_qa(cesta, [V(1, "Naši nejnovější generaci."), V(2, "Ahoj.")], zapsat=True)
    assert nacteno["1"]["zkomolena"] == ["generaci→genesraci"] and nacteno["2"]["zkomolena"] == []
    assert json.loads(cesta.read_text(encoding="utf-8"))["1"]["zkomolena"] == ["generaci→genesraci"]
    # uložené pole se nepřepisuje, dokud se nechce
    nacteno["1"]["zkomolena"] = ["x→y"]
    assert oznac_zkomolena(nacteno, {"1": "Naši nejnovější generaci."}, prepsat=False) is False
    assert oznac_zkomolena(nacteno, {"1": "Naši nejnovější generaci."}) is True


def test_skutecna_zakazka():
    """Kalibrace na zakázce PvFjbPYEOZI (1155 vět): musí chytit věty 171 a 1081, ne ~10 % vět."""
    if not (ZAKAZKA / "qa_zpet.json").exists():
        return
    job = json.loads((ZAKAZKA / "job.json").read_text(encoding="utf-8"))
    texty = {str(v["id"]): v["vyslov"] or v["cs"] for v in job["vety"]}
    zaloha = next(ZAKAZKA.glob("zaloha-*/job.json"), None)  # část vět má v běžící zakázce prázdný překlad
    if zaloha:
        for v in json.loads(zaloha.read_text(encoding="utf-8"))["vety"]:
            texty[str(v["id"])] = texty.get(str(v["id"])) or v["vyslov"] or v["cs"]
    data = json.loads((ZAKAZKA / "qa_zpet.json").read_text(encoding="utf-8"))
    oznac_zkomolena(data, texty)
    oznacene = {i for i, z in data.items() if z["zkomolena"]}
    assert "171" in oznacene and "1081" in oznacene
    assert data["171"]["zkomolena"] == ["generaci→genesraci"]
    assert len(oznacene) < 0.08 * len(data)


if __name__ == "__main__":
    for jmeno, fn in sorted(globals().items()):
        if jmeno.startswith("test_") and callable(fn):
            fn()
            print("ok", jmeno)
