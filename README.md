# Dabing lab

Nadabuje YouTube video (nebo vlastní soubor) do češtiny: přepíše řeč, rozliší mluvčí, přeloží
a namluví je hlasem naklonovaným z originálu včetně emocí. Všechno kromě překladu běží na tvém
Macu. Aplikace (Gradio) běží na **http://127.0.0.1:7872**.

Jen pro Mac s Apple Silicon (M1 a novější), macOS 14 nebo novější. Repozitář obsahuje jen kód:
modely, videa, zakázky ani přihlášení v něm nejsou.

## Instalace

Jeden příkaz nainstaluje všechno (Command Line Tools, Homebrew, nástroje, Python prostředí, na
požádání modely) a na konci otevře aplikaci. Účet na GitHubu nepotřebuješ:

```bash
curl -fsSL https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/instalovat.sh | bash
```

Skript se naklonuje do `~/dabing-lab` a před stažením modelů (zhruba 6,5 GB) se zeptá jednou.
`./instalovat.sh --zkouska` nic nezmění a jen vypíše, co by udělal; další přepínače: `--ano`,
`--bez-modelu`, `--bez-spusteni`, `-h`. Ruční postup (bez skriptu, začíná
`git clone https://github.com/christovnino-a11y/dabing-lab.git ~/dabing-lab`) je v
[docs/INSTALACE.md](docs/INSTALACE.md).

## Spuštění

```bash
dabing
```

Otevře prohlížeč na `http://127.0.0.1:7872` (okno Terminálu nech otevřené, `Ctrl+C` lab ukončí).
Příkaz `dabing` vytvoří instalace; bez něj `cd ~/dabing-lab && ./spustit.sh`.

## Aktualizace

Znovu spusť instalační příkaz z části Instalace: stáhne novou verzi kódu, doplní prostředí a modely
a hotové přeskočí.

## Požadavky

- **Mac s Apple Silicon (M1 a novější)** a **macOS 14 nebo novější**. Intel Mac ani Rosetta nepůjdou.
- **RAM:** 24 GB pohodlné, 16 GB minimum (videa zhruba do 25 až 30 minut, odhad), 8 GB nepoužitelné.
- **Volné místo:** 15 GB na instalaci (zabere zhruba 10 GB, z toho modely 6,5 GB), 30 GB na první
  zkoušky, 60 GB na hodinová videa.
- Homebrew, `ffmpeg`, `yt-dlp` (i s `deno`), `uv`, `git`: nainstaluje `instalovat.sh`. Python 3.12 si `uv` stáhne samo.
- Překladač (volí se v aplikaci): **Codex** (účet ChatGPT, nejlepší překlad, do OpenAI jde jen text, ne
  zvuk), nebo lokálně **Ollama** či **Bionic / LM Studio** (nic neodchází z Macu). Bionic je nová
  aplikace LM Studio; její server zapneš v části Local Model API.
- Dabing trvá zhruba 3 až 8násobek délky videa (měření na M4 Pro, 24 GB RAM).

## Po instalaci

1. **Modely** (zhruba 6,5 GB) jsou ve standardní cache Hugging Face, protože lab běží offline
   (`HF_HUB_OFFLINE=1`). `./scripts/stahni_modely.sh --list` jen vypíše tabulku. Bez povinných
   modelů `./spustit.sh` odmítne nastartovat.
2. **Kdo překládá** se volí v aplikaci (sekce „Kdo překládá"): Codex, Ollama nebo Bionic / LM Studio. Aplikace
   u každé volby ukáže stav a další krok. Přihlášení ke Codexu se dělá tamtéž účtem ChatGPT
   (tlačítko „Přihlásit kódem" nebo „Přihlásit v prohlížeči"); žádné `npm i -g @openai/codex` ani
   symlink na `~/.codex/auth.json` nejsou potřeba. Přihlášení se uloží do `codex_home/auth.json`
   (v `.gitignore`, nikomu ho neposílej). Lokální model (Ollama, Bionic / LM Studio) si stáhni podle paměti
   svého Macu, viz [docs/INSTALACE.md](docs/INSTALACE.md), kapitola 4.
3. První zkoušku udělej na vlastní nahrávce (1 až 2 minuty), viz kapitola 5 v
   [docs/INSTALACE.md](docs/INSTALACE.md).

## Pomoc od AI asistenta

- **Claude Code** (nebo jiný agent, který umí spouštět příkazy): nainstaluj ho
  (<https://claude.com/product/claude-code>), proveď instalaci výše, pak ve složce `dabing-lab`
  spusť `claude` a napiš „Nainstaluj dabing lab". Skill
  [.claude/skills/dabing-lab/SKILL.md](.claude/skills/dabing-lab/SKILL.md) se načte ze složky projektu
  a ptá se před každým velkým stažením. Interaktivní příkazy (heslo, přihlášení, [y/N]) spouštíš ty
  v samostatném okně Terminálu. Agenty Codexu a další odkazuje [AGENTS.md](AGENTS.md).
- **ChatGPT nebo Claude v prohlížeči** (nevidí tvůj počítač): otevři
  [docs/chatgpt-instrukce.md](https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/docs/chatgpt-instrukce.md),
  zkopíruj celý text a vlož ho jako první zprávu nového chatu (nejspolehlivější), nebo do Projectu či
  Custom GPT. Do Claude Projectu lze nahrát i soubor `.claude/skills/dabing-lab/SKILL.md`.
  Asistent v prohlížeči příkazy nespouští, jen tě vede a čte výpisy, které mu vložíš.

## Jak to funguje

| Krok | Nástroj | Kde |
|---|---|---|
| Stažení | yt-dlp (H.264 do 1080p) | `dabing/media.py` |
| Hlas vs. pozadí | Demucs `hf://htdemucs` (MPS) | `dabing/stages/separate.py` |
| Přepis se slovními časy | Parakeet TDT 0.6B v3 (MLX) | `dabing/stages/asr_local.py` |
| Mluvčí | Nemotron-3-Diarization | `dabing/stages/diar.py` |
| Věty pro dabing | vlastní skládání ze slov | `dabing/segmenty.py` |
| Překlad + výslovnost | Codex (dávkově), Ollama nebo Bionic / LM Studio | `dabing/translate.py` |
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
- **Kontroly**: lab zjistí pohlaví z obrazu u každého mluvčího (jen s Codexem a zaškrtnutým políčkem),
  sloučí rozpadlé mluvčí, po překladu zkontroluje shodu pohlaví a hlídá zkomolená slova, která
  přednese znovu. Lokální překladač nic neposílá ven, a proto kontrola podle snímků v něm odpadá.
- **Překlad**: Codex je nejlepší a umí ověřit jména na webu; lokální modely jména neověřují.
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

## Licence

- Kód labu: [PolyForm Noncommercial 1.0.0](LICENSE). Zdarma k používání a úpravám, ne komerčně.
- Modely mají vlastní licence (přehled a velikosti v [docs/INSTALACE.md](docs/INSTALACE.md),
  kapitola 3; před sdílením výsledků přečti kartu každého modelu).
- Váhy OmniVoice jsou **CC-BY-NC**, váhy BandIt Plus jsou odvozené od modelu CC-BY-NC-4.0: dabing je
  **jen nekomerční**. Monetizace dabovaného videa je komerční použití.
- Přibalený kód BandIt (`dabing/vendor/bandit`) si ponechává své licence, viz tamní soubory LICENSE.
- Parakeet TDT v3 (CC-BY-4.0), OmniVoice a BandIt Plus vyžadují uvedení původu. Podsložka
  `audio_tokenizer/` modelu OmniVoice je pod Boson Higgs Audio 2 Community License, která při
  šíření modelu nebo produktu s ním vyžaduje uvedení „Built with Higgs Materials licensed from
  Boson AI USA, Inc."

## Upozornění

Lab naklonuje hlas **každého mluvčího** v dabovaném videu, proto potřebuješ souhlas všech mluvčích
(u vlastní nahrávky od sebe a lidí, kteří v ní mluví) a musíš respektovat autorská práva videa
a podmínky YouTube. Za použití odpovídáš ty; dabing označ jako vytvořený umělou inteligencí.
Technická upozornění, ne právní rada.

## Poznámky

- **Volitelné sdílení přihlášení s Codex CLI:** `codex_home/auth.json` může být odkaz na
  `~/.codex/auth.json`. Běžný uživatel ho **nevytváří**: přihlásí se přímo v UI labu.
- Složku projektu po instalaci nepřesouvej (obě `.venv` mají zapsané absolutní cesty; po přesunu je
  smaž a vytvoř znovu). Obě prostředí musí ležet přesně v `.venv/` a `worker/omnivoice/.venv/`.
- BandIt Plus patří do `modely/bandit_plus/` a Silero VAD do
  `~/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/` (cesta je pevná v
  `dabing/config.py`). `modely/` je v `.gitignore`; váhy ani média nikdy nepatří do gitu.
- Obě volitelné součásti (BandIt, Silero) při absenci jen zhorší kvalitu, dabing doběhne.
