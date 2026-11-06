"""Testy lokálních překladačů (dabing/lokalni.py), nastavení a normalizace starých zakázek.

Bez modelů a bez sítě: Ollama a LM Studio hraje atrapa HTTP serveru ve vlákně.
Spuštění: .venv/bin/python tests/test_lokalni.py   (nebo pytest)
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dabing import job, lokalni, nastaveni  # noqa: E402

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


class Atrapa:
    """HTTP server s předpřipravenými odpověďmi; zaznamenává příchozí požadavky."""

    def __init__(self, get=None, post=None):
        self.get, self.post, self.pozadavky = get or {}, list(post or []), []
        atrapa = self

        class H(BaseHTTPRequestHandler):
            def _posli(self, kod, telo):
                data = json.dumps(telo).encode()
                self.send_response(kod)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path in atrapa.get:
                    self._posli(200, atrapa.get[self.path])
                else:
                    self._posli(404, {})

            def do_POST(self):
                telo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                atrapa.pozadavky.append((self.path, telo))
                kod, odpoved = atrapa.post.pop(0) if atrapa.post else (200, {})
                self._posli(kod, odpoved)

            def log_message(self, *_):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


def _env(sluzba, url):
    os.environ["DABING_OLLAMA_URL" if sluzba == "ollama" else "DABING_LMSTUDIO_URL"] = url


def _ollama_odpoved(obsah):
    return (200, {"message": {"role": "assistant", "content": obsah}})


def test_stav_ollama():
    with Atrapa(get={"/api/tags": {"models": [{"name": "model-a"}, {"name": "nomic-embed-text"}]}}) as a:
        _env("ollama", a.url)
        s = lokalni.stav("ollama")
        assert s.bezi and s.modely == ["model-a"] and s.popis.startswith("Připraveno, 1 model")
    with Atrapa(get={"/api/tags": {"models": []}}) as a:
        _env("ollama", a.url)
        s = lokalni.stav("ollama")
        assert s.bezi and s.modely == [] and "ollama pull" in s.popis


def test_stav_lmstudio():
    with Atrapa(get={"/v1/models": {"data": [{"id": "m1"}, {"id": "text-embedding-x"}, {"id": "m2"}]}}) as a:
        _env("lmstudio", a.url)
        s = lokalni.stav("lmstudio")
        assert s.bezi and s.modely == ["m1", "m2"] and "2 modely" in s.popis
    with Atrapa(get={"/v1/models": {"data": [{"id": "embed-only"}]}}) as a:
        _env("lmstudio", a.url)
        s = lokalni.stav("lmstudio")
        assert s.bezi and not s.modely and "model" in s.popis


def test_stav_neberi():
    with Atrapa() as a:
        url = a.url
    _env("ollama", url)
    s = lokalni.stav("ollama")
    assert not s.bezi and s.modely == [] and s.popis


def test_ollama_nativni_telo():
    with Atrapa(post=[_ollama_odpoved('{"x": "ahoj"}')]) as a:
        _env("ollama", a.url)
        assert lokalni.dotaz("ollama", "m", "sys", "zadani", SCHEMA) == {"x": "ahoj"}
        cesta, telo = a.pozadavky[0]
    assert cesta == "/api/chat"
    assert telo["model"] == "m" and telo["stream"] is False and telo["think"] is False
    assert telo["format"] == SCHEMA
    assert telo["options"] == {"temperature": 0.3, "num_ctx": 16384, "num_predict": 12000}
    assert [m["role"] for m in telo["messages"]] == ["system", "user"]


def test_ollama_retry_pri_400():
    with Atrapa(post=[(400, {"error": "think nepodporováno"}), _ollama_odpoved('{"x": "b"}')]) as a:
        _env("ollama", a.url)
        assert lokalni.dotaz("ollama", "m", "sys", "z", SCHEMA) == {"x": "b"}
        telo1, telo2 = a.pozadavky[0][1], a.pozadavky[1][1]
    assert "think" in telo1 and "think" not in telo2 and telo2["format"] == SCHEMA


def test_ollama_retry_pri_prazdne_odpovedi_az_bez_formatu():
    with Atrapa(post=[_ollama_odpoved(""), _ollama_odpoved("  "), _ollama_odpoved('{"x": "c"}')]) as a:
        _env("ollama", a.url)
        assert lokalni.dotaz("ollama", "m", "sys", "z", SCHEMA) == {"x": "c"}
        t = [p[1] for p in a.pozadavky]
    assert "think" in t[0] and "think" not in t[1] and "format" in t[1]
    assert "format" not in t[2] and "think" not in t[2]
    assert "Odpověz jen JSON podle schématu" in t[2]["messages"][0]["content"]


def test_ollama_vsechno_selze():
    with Atrapa(post=[_ollama_odpoved("")] * 3) as a:
        _env("ollama", a.url)
        try:
            lokalni.dotaz("ollama", "m", "sys", "z", SCHEMA)
        except RuntimeError as chyba:
            assert "prázdnou" in str(chyba)
        else:
            raise AssertionError("měla vyletět chyba")


def test_json_v_ohradach():
    assert lokalni._json('```json\n{"x": "a"}\n```', {}) == {"x": "a"}
    assert lokalni._json('Tady je to: {"x": "a"} hotovo', {}) == {"x": "a"}
    with Atrapa(post=[_ollama_odpoved('```json\n{"x": "d"}\n```')]) as a:
        _env("ollama", a.url)
        assert lokalni.dotaz("ollama", "m", "s", "z", SCHEMA) == {"x": "d"}


def test_chybi_model_a_spojeni():
    with Atrapa(post=[(404, {"error": "model not found"})]) as a:
        _env("ollama", a.url)
        try:
            lokalni.dotaz("ollama", "neni", "s", "z", SCHEMA)
        except RuntimeError as chyba:
            assert "neni" in str(chyba)
        else:
            raise AssertionError
    with Atrapa() as a:
        url = a.url
    _env("lmstudio", url)
    try:
        lokalni.dotaz("lmstudio", "m", "s", "z", SCHEMA)
    except RuntimeError as chyba:
        assert "LM Studio neběží" in str(chyba)
    else:
        raise AssertionError


def test_lmstudio_telo():
    odpoved = (200, {"choices": [{"message": {"content": '{"x": "e"}'}}]})
    with Atrapa(post=[odpoved]) as a:
        _env("lmstudio", a.url)
        assert lokalni.dotaz("lmstudio", "m", "sys", "z", SCHEMA) == {"x": "e"}
        cesta, telo = a.pozadavky[0]
    assert cesta == "/v1/chat/completions"
    assert telo["temperature"] == 0.3 and telo["max_tokens"] == 12000
    rf = telo["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA


def test_odpoved_v_poli_premysleni():
    """Modely s přemýšlením: LM Studio dá JSON do reasoning_content a content nechá prázdný."""
    odpoved = (200, {"choices": [{"message": {"content": "", "reasoning_content": '{"x": "f"}'}}]})
    with Atrapa(post=[odpoved]) as a:
        _env("lmstudio", a.url)
        assert lokalni.dotaz("lmstudio", "model", "s", "z", {"type": "object"}) == {"x": "f"}


def test_odpoved_mimo_schema_se_opakuje():
    """Holé pole ani kus JSONu z přemýšlení nejsou překlad: další pokus, ne tichý „úspěch"."""
    schema = {"type": "object", "required": ["vety"]}
    with Atrapa(post=[_ollama_odpoved('[{"id": 1}]'), _ollama_odpoved('{"id": 3}'),
                      _ollama_odpoved('{"vety": []}')]) as a:
        _env("ollama", a.url)
        assert lokalni.dotaz("ollama", "m", "s", "z", schema) == {"vety": []}
    odpoved = (200, {"choices": [{"message": {"content": "", "reasoning_content": 'Přemýšlím: {"id": 3}'}}]})
    with Atrapa(post=[odpoved]) as a:
        _env("lmstudio", a.url)
        try:
            lokalni.dotaz("lmstudio", "m", "s", "z", schema)
        except RuntimeError as chyba:
            assert "použitelnou" in str(chyba)
        else:
            raise AssertionError("odpověď mimo schéma prošla")


def test_cloudove_modely_ollamy_se_nenabizeji():
    tags = {"models": [{"name": "maly:4b"}, {"name": "velky:31b-cloud"}]}
    with Atrapa(get={"/api/tags": tags}) as a:
        _env("ollama", a.url)
        assert lokalni.stav("ollama").modely == ["maly:4b"]


def test_adresa_s_v1():
    """Bionic ukazuje http://localhost:1234/v1, cesty API už /v1 obsahují."""
    assert lokalni.ocisti_adresu("http://localhost:1234/v1/") == "http://localhost:1234"
    assert lokalni.ocisti_adresu("localhost:11434") == "http://localhost:11434"
    assert lokalni.ocisti_adresu("") == ""


def test_nastaveni_poskozeny_soubor():
    slozka = Path(tempfile.mkdtemp())
    f = slozka / "nastaveni.json"
    assert nastaveni.nacti(f) == {}  # chybí
    f.write_text("{rozbité", encoding="utf-8")
    assert nastaveni.nacti(f) == {} and nastaveni.ulozeny_prekladac(f) == ""
    f.write_text("[1, 2]", encoding="utf-8")
    assert nastaveni.nacti(f) == {}
    nastaveni.uloz_volbu(prekladac="ollama", soubor=f)
    nastaveni.uloz_volbu(sluzba="ollama", model="m1", soubor=f)
    nastaveni.uloz_volbu(sluzba="lmstudio", model="m2", soubor=f)
    assert nastaveni.ulozeny_prekladac(f) == "ollama"
    assert nastaveni.ulozeny_model("ollama", f) == "m1" and nastaveni.ulozeny_model("lmstudio", f) == "m2"
    assert not list(slozka.glob("*.tmp"))
    f.write_text(json.dumps({"prekladac": "neznamy"}), encoding="utf-8")
    assert nastaveni.ulozeny_prekladac(f) == ""


def test_stara_zakazka_offline():
    assert job.normalizuj_prekladac("offline") == "lmstudio"
    assert job.normalizuj_prekladac("codex") == "codex" and job.normalizuj_prekladac("ollama") == "ollama"
    # načtení ze složky mimo jobs/ (nacti jen čte)
    slozka = Path(tempfile.mkdtemp())
    (slozka / "job.json").write_text(json.dumps({"id": "t", "zdroj": "x", "prekladac": "offline",
                                                 "neznamy_klic": 1, "vety": []}), encoding="utf-8")
    z = job.Zakazka.nacti(slozka)
    assert z.prekladac == "lmstudio" and z.model_prekladu == ""


def test_snimky_jen_pro_codex_a_davky():
    from dabing import translate
    z = job.Zakazka(id="t", zdroj="x", prekladac="ollama")
    assert translate._lokalni_preklad(z) and translate._davka_znaku(z) == translate.DAVKA_ZNAKU_OFFLINE
    z.prekladac = "codex"
    assert not translate._lokalni_preklad(z) and translate._davka_znaku(z) == translate.DAVKA_ZNAKU_CODEX
    zdroj = (Path(__file__).resolve().parent.parent / "dabing" / "pipeline.py").read_text(encoding="utf-8")
    assert 'if z.snimky_pohlavi and z.prekladac == "codex":' in zdroj


# ------------------------------------------------------------------ adresa API služby
def _bez_env():
    for k in ("DABING_OLLAMA_URL", "DABING_LMSTUDIO_URL"):
        os.environ.pop(k, None)


def _s_nastavenim(fn):
    """Spustí fn s dočasným souborem nastavení (skutečný nastaveni.json se nedotkne)."""
    puvodni = nastaveni.SOUBOR
    nastaveni.SOUBOR = Path(tempfile.mkdtemp()) / "nastaveni.json"
    _bez_env()
    try:
        fn()
    finally:
        nastaveni.SOUBOR = puvodni
        _bez_env()


def test_adresa_vychozi_prazdne_pole_a_env():
    def beh():
        assert lokalni._url("ollama") == "http://127.0.0.1:11434"
        nastaveni.uloz_adresu("ollama", "  http://10.0.0.5:11434/ ")
        assert nastaveni.ulozena_adresa("ollama") == "http://10.0.0.5:11434"
        assert lokalni._url("ollama") == "http://10.0.0.5:11434" and lokalni._url("lmstudio").endswith(":1234")
        nastaveni.uloz_adresu("lmstudio", "nas:1234")  # bez schématu se doplní
        assert nastaveni.ulozena_adresa("lmstudio") == "http://nas:1234"
        os.environ["DABING_OLLAMA_URL"] = "http://127.0.0.1:9"  # proměnná prostředí má přednost
        assert lokalni._url("ollama") == "http://127.0.0.1:9"
        _bez_env()
        nastaveni.uloz_adresu("ollama", "")  # prázdné pole = výchozí
        assert nastaveni.ulozena_adresa("ollama") == "" and lokalni._url("ollama") == "http://127.0.0.1:11434"
        nastaveni.uloz_adresu("lmstudio", "http://127.0.0.1:1234")  # výchozí se neukládá
        assert nastaveni.ulozena_adresa("lmstudio") == ""
    _s_nastavenim(beh)


def test_adresa_stav_a_dotaz_pouzivaji_ulozenou():
    def beh():
        with Atrapa(get={"/api/tags": {"models": [{"name": "m"}]}}, post=[_ollama_odpoved('{"x": "a"}')]) as a:
            nastaveni.uloz_adresu("ollama", a.url)
            s = lokalni.stav("ollama")
            assert s.bezi and s.modely == ["m"]
            assert lokalni.dotaz("ollama", "m", "s", "z", SCHEMA) == {"x": "a"} and a.pozadavky[0][0] == "/api/chat"
    _s_nastavenim(beh)


def test_adresa_mimo_mac_varovani_a_zapnuti():
    def beh():
        assert lokalni.je_na_tomto_macu("ollama")
        nastaveni.uloz_adresu("ollama", "http://localhost:11434")
        assert lokalni.je_na_tomto_macu("ollama")
        nastaveni.uloz_adresu("ollama", "http://192.0.2.1:11434")
        assert not lokalni.je_na_tomto_macu("ollama")
        assert "mimo tento Mac" in _obnov_text("ollama")
        assert "Zapnout" not in lokalni.zapni("ollama") and "spusť ho na tom počítači" in lokalni.zapni("ollama")
        nastaveni.uloz_adresu("ollama", "")
        assert "mimo tento Mac" not in _obnov_text("ollama")
    _s_nastavenim(beh)


def _obnov_text(sluzba):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import app
    return app.obnov_prekladac(sluzba)[0]


def test_adresa_neni_v_zakazce():
    nazvy = {f.name for f in job.fields(job.Zakazka)}
    assert not any("adres" in n or "url" in n for n in nazvy)


if __name__ == "__main__":
    testy = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for jmeno, fn in testy:
        fn()
        print("ok", jmeno)
    print(f"{len(testy)} testů prošlo")
