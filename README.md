# Dabing lab

YouTube video (nebo vlastní soubor) do češtiny: přepis, mluvčí, překlad a hlas
naklonovaný z originálu včetně emocí. Gradio na **http://127.0.0.1:7872**,
spouští se příkazem `./spustit.sh`.

Jen pro Mac s Apple Silicon. Repozitář je soukromý a obsahuje jen kód: modely, videa,
zakázky ani přihlášení v něm nejsou.

## Požadavky

- **Mac s Apple Silicon (M1 a novější)** a **macOS 14 nebo novější**. Intel Mac ani Rosetta nepůjdou.
- **RAM:** 24 GB pohodlné, 16 GB minimum (videa zhruba do 25 až 30 minut, odhad), 8 GB nepoužitelné.
- **Volné místo:** 15 GB na instalaci (zabere zhruba 10 GB, z toho modely 6,5 GB), 30 GB na první
  zkoušky, 60 GB na hodinová videa.
- Homebrew, `ffmpeg`, `yt-dlp` (i s `deno`), `uv`, `git`. Python 3.12 si `uv` stáhne samo.
- Účet ChatGPT pro překlad Codexem (nebo LM Studio pro režim „Plně lokální").
- Dabing trvá zhruba 3 až 8násobek délky videa (měření na M4 Pro, 24 GB RAM).

## Rychlý start

Podrobný návod pro čistý Mac je v **[docs/INSTALACE.md](docs/INSTALACE.md)**. Na čistém Macu
(bez Homebrew a gitu) začni tam od kroku 1: zkratka níže je pro ty, kdo už mají Command Line Tools
a Homebrew. Terminál otevřeš přes Spotlight (Cmd+Mezerník, napiš Terminal, Enter). Vkládej vždy
**jen jeden blok** a počkej, až doběhne, než vložíš další.

```bash
brew install ffmpeg yt-dlp uv gh
```

`yt-dlp` přitáhne i `deno`. Pak přihlášení k GitHubu (interaktivní, dělej sám):

```bash
gh auth login
```

Vyber GitHub.com, HTTPS, na dotaz o Git credentials odpověz Y, pak Login with a web browser a opiš
kód z terminálu do prohlížeče. Repozitář je soukromý, potřebuješ přijatou pozvánku (v e-mailu od
GitHubu klikni na View invitation a Accept). Místo `<VLASTNIK>` dosaď jméno vlastníka z odkazu v pozvánce:

```bash
gh repo clone <VLASTNIK>/dabing-lab
cd dabing-lab
```

Python prostředí: hlavní (`.venv`) a pro hlas (`worker/omnivoice/.venv`). Příkaz jde bezpečně
zopakovat (oprava, aktualizace):

```bash
if [ -f uv.lock ]; then uv sync --frozen; else uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml; fi
(cd worker/omnivoice && uv sync)
```

Modely po jednom (u každého ukáže velikost a licenci a zeptá se [y/N]; `--list` jen vypíše
tabulku), kontrola a spuštění:

```bash
./scripts/stahni_modely.sh
./spustit.sh --kontrola
./spustit.sh
```

Poslední příkaz spustí lab a otevře prohlížeč. Okno Terminálu nech otevřené, dokud lab používáš
(`Ctrl+C` ho ukončí). Příště: `cd ~/dabing-lab && ./spustit.sh` (cesta podle toho, kam jsi
repozitář naklonoval).

1. **Modely** (zhruba 6,5 GB) se stahují do standardní cache Hugging Face, protože lab běží offline
   (`HF_HUB_OFFLINE=1`). `./scripts/stahni_modely.sh --list` jen vypíše tabulku a nic nestáhne.
   Bez povinných modelů `./spustit.sh` odmítne nastartovat.
2. **Přihlášení ke Codexu se dělá v UI labu**: sekce „Codex účet (překlad)", tlačítko
   „Přihlásit kódem" nebo „Přihlásit v prohlížeči", účtem ChatGPT. Žádné `npm i -g @openai/codex`
   a žádný symlink na `~/.codex/auth.json` nejsou potřeba. Přihlášení se uloží do
   `codex_home/auth.json` (v `.gitignore`, nikomu ho neposílej).
3. První zkoušku udělej na vlastní nahrávce (1 až 2 minuty), viz kapitola 5 v
   [docs/INSTALACE.md](docs/INSTALACE.md).

## Pomoc od AI asistenta

Skill i návod pro ChatGPT leží v soukromém repozitáři. **Skill pro Claude Code je tedy k dispozici
až po naklonování (krok 4 v [docs/INSTALACE.md](docs/INSTALACE.md))**. Kroky 1 až 4 proveď ručně
podle návodu, nebo s ChatGPT či Claude v prohlížeči: po přijetí pozvánky otevři na github.com
soubor `docs/chatgpt-instrukce.md`, klikni na Raw (nebo ikonu kopírování), zkopíruj celý text a vlož
ho podle návodu v tom souboru (asistent v prohlížeči příkazy nespouští, jen tě vede a čte výpisy,
které mu vložíš). Vlastník ti může soubor `docs/chatgpt-instrukce.md` nebo `.claude/skills/dabing-lab/SKILL.md`
poslat i s pozvánkou.

- **Claude Code** (nebo jiný agent, který umí spouštět příkazy): nainstaluj ho
  (<https://claude.com/product/claude-code>), proveď ručně kroky 1 až 4, pak ve složce `dabing-lab`
  spusť `claude` a napiš „Nainstaluj dabing lab". Skill
  [.claude/skills/dabing-lab/SKILL.md](.claude/skills/dabing-lab/SKILL.md) se načte ze složky projektu,
  ptá se před každým velkým stažením. Interaktivní příkazy (heslo, přihlášení, [y/N]) spouštíš ty
  v samostatném okně Terminálu. Agenty Codexu a další odkazuje [AGENTS.md](AGENTS.md).
- **ChatGPT nebo Claude v prohlížeči** (nevidí tvůj počítač): text z
  [docs/chatgpt-instrukce.md](docs/chatgpt-instrukce.md) vlož jako první zprávu nového chatu
  (nejspolehlivější), nebo do Projectu či Custom GPT. Do Claude Projectu lze nahrát i soubor
  `.claude/skills/dabing-lab/SKILL.md`.

## Jak to funguje

| Krok | Nástroj | Kde |
|---|---|---|
| Stažení | yt-dlp (H.264 do 1080p) | `dabing/media.py` |
| Hlas vs. pozadí | Demucs `hf://htdemucs` (MPS) | `dabing/stages/separate.py` |
| Přepis se slovními časy | Parakeet TDT 0.6B v3 (MLX) | `dabing/stages/asr_local.py` |
| Mluvčí | Nemotron-3-Diarization | `dabing/stages/diar.py` |
| Věty pro dabing | vlastní skládání ze slov | `dabing/segmenty.py` |
| Překlad + výslovnost | Codex (dávkově) nebo LM Studio | `dabing/translate.py` |
| Hlas | OmniVoice klon (worker, vlastní venv) | `dabing/tts.py`, `worker/omnivoice/` |
| Kontrola srozumitelnosti | zpětný přepis Parakeetem (CER) | `dabing/stages/qa_zpet.py` |
| Detekce řeči (ořez konců slov) | Silero VAD (CPU) | `dabing/stages/vad.py` |
| Mix a export | podklad = originál − hlas mluvčího, MP4 + WAV + SRT + podklad FLAC | `dabing/mix.py`, `dabing/maska.py` |

Zakázka = složka `jobs/<ID videa>/`. Hotové kroky se přeskakují; po úpravě
překladu v tabulce se přegenerují jen změněné věty.

## Kvalita

- **Hlas**: výchozí je „kotva": každý mluvčí nejdřív svým naklonovaným hlasem namluví pevnou větu
  v cílovém jazyce (ze tří pokusů vyhraje nejpodobnější) a jeho věty se klonují z ní. Hlas zůstane jeho
  a výslovnost je rodilá, emoce jednotlivých vět se ale přenášejí méně. U blízkých jazyků (čeština,
  slovenština) se klonuje přímo z jeho původních vět. Hlídač identity (ECAPA) věty, které hlasem
  vybočí, přegeneruje.
- **Délka**: překlad cílí na ~95 % času originálu (OmniVoice mluví česky zhruba 17,5 znaku/s).
  Dlouhé věty Codex zkrátí, krátké se mírně zpomalí (nejvýš na 0,8×), nic se nezrychluje
  víc než 1/0,85.
- **Výslovnost**: Codex vrací ke každé větě i `vyslov` (cizí jména foneticky: „Mjúz",
  „ví em", „éj áj"); titulky zůstávají normálně.

- **Ostatní zvuky**: odečítá se jen hlas mluvčího a jen tam, kde opravdu mluví (slova
  z přepisu, konce oříznuté detektorem řeči). Jinde hraje originál beze změny, takže
  publikum, hudba i ruchy zůstanou. Krátké pauzy mluvčího se vyplní (podklad nepumpuje),
  pokud v nich nezazní nový zvuk. Podklad se nikdy neztišuje ani nekomprimuje; je ke
  stažení jako `*.podklad.flac` (originál bez dabovaného mluvčího — nepřeložená řeč v něm
  zůstává). Limit: co Demucs přiřadí hlasu (jásot nebo zpěv přímo přes řeč), zmizí s ním.

## Prostředí

- `.venv` — UI a orchestrace + podprocesy (torch 2.13, transformers z gitu kvůli Nemotronu,
  parakeet-mlx, demucs 4.1.0, openai-codex 0.154.0). Gradio proces torch ani mlx nenačítá.
- `worker/omnivoice/.venv` — OmniVoice 0.2.1, torch 2.8, speechbrain. Běží odděleně,
  protože chyba MPS alokátoru shodí celý proces; lab ho pak jen restartuje.
- `codex_home/` — izolovaná konfigurace Codexu (bez skills, MCP a AGENTS.md), jen předplatné
  ChatGPT, žádné API klíče. Přihlášení se dělá v UI a ukládá se do `codex_home/auth.json`.
- Modely jen z lokální HF cache (`HF_HUB_OFFLINE=1`), nic se nestahuje. Stahují se předem
  skriptem `scripts/stahni_modely.sh`.

## Licence a právo

Technická upozornění, ne právní rada:

- Váhy OmniVoice jsou **CC-BY-NC**, váhy BandIt Plus jsou odvozené od modelu CC-BY-NC-4.0:
  dabing **jen pro nekomerční použití**. Monetizace dabovaného videa je komerční použití.
- Lab naklonuje hlas **každého mluvčího** v dabovaném videu. Souhlas proto potřebuješ od všech
  mluvčích (u vlastní nahrávky od sebe a lidí, kteří v ní mluví). U cizího videa bez jejich souhlasu
  dabing nezveřejňuj.
- Respektuj autorská práva dabovaného videa a podmínky YouTube.
- Dabing označ jako vytvořený umělou inteligencí.
- Uvedení autorů: Parakeet TDT v3 (CC-BY-4.0, NVIDIA), OmniVoice (CC-BY-NC, k2-fsa) a BandIt Plus
  (CC-BY-NC-4.0) vyžadují uvedení původu. Podsložka `audio_tokenizer/` modelu OmniVoice je pod Boson
  Higgs Audio 2 Community License, která při šíření modelu nebo produktu s ním vyžaduje uvedení
  „Built with Higgs Materials licensed from Boson AI USA, Inc." (plné znění je v souboru LICENSE na
  kartě modelu). Při sdílení výsledků nebo modelů přečti podmínky na kartě každého modelu.
  Licence všech modelů a velikosti jsou v [docs/INSTALACE.md](docs/INSTALACE.md), kapitola 3.

## Poznámky

- **Symlink `~/.codex/auth.json` je jen pro vlastníka repozitáře.** Lab umí volitelně sdílet přihlášení
  s vlastním Codex CLI tak, že `codex_home/auth.json` je odkaz na `~/.codex/auth.json`. Nový uživatel
  ho **nevytváří**: přihlásí se přímo v UI labu.
- Složku projektu po instalaci nepřesouvej (obě `.venv` mají zapsané absolutní cesty; po přesunu je
  smaž a vytvoř znovu). Obě prostředí musí ležet přesně v `.venv/` a `worker/omnivoice/.venv/`.
- BandIt Plus patří do `modely/bandit_plus/` a Silero VAD do
  `~/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/` (cesta je pevná v
  `dabing/config.py`). `modely/` je v `.gitignore`; váhy ani média nikdy nepatří do gitu.
- Obě volitelné součásti (BandIt, Silero) při absenci jen zhorší kvalitu, dabing doběhne.
