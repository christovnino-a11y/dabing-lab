# Instalace Dabing labu (macOS, Apple Silicon)

Dabing lab je místní webová aplikace (Gradio, adresa `http://127.0.0.1:7872`), která nadabuje
video z YouTube nebo vlastní soubor do jiného jazyka (hlavně češtiny): stáhne video, oddělí hlas
od pozadí, přepíše řeč, rozliší mluvčí, přeloží a namluví hlasem naklonovaným z originálu.
Všechno kromě překladu běží na tvém Macu.

Tento návod je pro čistý Mac. Můžeš ho projít ručně, nebo ti s ním pomůže AI asistent:

- **ChatGPT nebo Claude v prohlížeči** (nevidí tvůj počítač, jen s tebou píše a čte výpisy, které mu
  vložíš): po přijetí pozvánky otevři na github.com soubor `docs/chatgpt-instrukce.md`, klikni na Raw
  (nebo ikonu kopírování), zkopíruj celý text a vlož ho podle návodu v tom souboru. Funguje to i
  v Claude Projectu na claude.ai (ten příkazy nespouští, jen tě vede).
- **Claude Code** (spouští příkazy za tebe): nainstaluj ho (<https://claude.com/product/claude-code>),
  proveď ručně kroky 1 až 4 (nejlíp s asistentem z předchozího bodu), pak `cd ~/dabing-lab && claude`
  a napiš „Pomoz mi nainstalovat Dabing lab". Skill
  [`.claude/skills/dabing-lab/SKILL.md`](../.claude/skills/dabing-lab/SKILL.md) je součástí
  repozitáře, takže je k dispozici **až po kroku 4 (naklonování)**. Bez Claude Code nahraj
  `.claude/skills/dabing-lab/SKILL.md` do Claude Projectu, nebo ho vlož do chatu. Vlastník ti ho
  (i `chatgpt-instrukce.md`) může poslat spolu s pozvánkou.

Interaktivní věci (heslo k Macu, přihlášení, otázky [y/N]) děláš vždy ty sám v **samostatném okně
aplikace Terminál**, ne přes nástroj asistenta.

Terminál otevřeš přes Spotlight (Cmd+Mezerník, napiš Terminal, Enter). Do okna vkládej příkazy
(Cmd+V) a potvrzuj Enterem. Příkazy jsou pro `zsh` (výchozí terminál macOS). Vkládej vždy **jen
jeden blok** a počkej, až doběhne, než vložíš další. Kde blok čeká na tvůj vstup (heslo, volby,
Enter), je to u něj napsáno.

## Instalace jedním příkazem

Repozitář je soukromý: potřebuješ přijatou pozvánku a přihlášené `gh` (`brew install gh`, pak
`gh auth login`). Pak stačí:

```bash
gh repo clone christovnino-a11y/dabing-lab ~/dabing-lab && ~/dabing-lab/instalovat.sh
```

Až bude repozitář veřejný, jde i `curl -fsSL https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/instalovat.sh | bash`
(naklonuje se do `~/dabing-lab`, jinou složku zvolí `DABING_SLOZKA`).

Skript má osm kroků: kontrola Macu (arm64, macOS 14+, aspoň 15 GB volného místa, u aktualizace 2 GB), Command Line
Tools, Homebrew, chybějící nástroje (ffmpeg, yt-dlp, deno, uv), `git pull --ff-only` (jen v čisté
složce na `main`), Python prostředí, modely (jedna otázka, zhruba 6,5 GB; váhy OmniVoice jsou
CC-BY-NC, tedy jen nekomerčně) a příkaz `dabing` (odkaz `~/.local/bin/dabing`, případně řádek v
`~/.zshrc` po upozornění). Pak spustí `./spustit.sh --kontrola` a lab. Hotové kroky přeskočí, takže
je to zároveň aktualizace. Ollamu ani LM Studio neinstaluje: překladač se volí v aplikaci.

Přepínače: `--zkouska` (nic nezmění, jen vypíše, co by udělal), `--ano` (bez otázek, včetně
stažení modelů), `--bez-modelu`, `--bez-spusteni`, `-h`. Bez terminálu a bez `--ano` se nic
nestahuje. Heslo k Macu (Homebrew) a přihlášení zadáváš sám v okně Terminálu.

Níže je **ruční postup** (kapitola 2), kdyby skript selhal nebo ses chtěl rozhodovat po krocích.

## 1. Požadavky

| Položka | Požadavek |
|---|---|
| Počítač | **Mac s Apple Silicon (M1 a novější).** Intel Mac ani Rosetta nepůjdou (torch a MLX mají jen buildy pro arm64). |
| macOS | **14 (Sonoma) nebo novější.** |
| Paměť RAM | 24 GB je pohodlné (na něm doběhlo i 54minutové video). **16 GB je minimum**, videa zhruba do 25 až 30 minut (odhad), delší půjdou přes swap pomalu nebo selžou. 8 GB je nepoužitelné. Lokální překladač (Ollama, LM Studio) zabere RAM navíc a dává smysl až od 24 GB. |
| Volné místo | Instalace zabere zhruba 10 GB (modely 6,5 GB, prostředí 2,6 GB, nástroje 0,7 GB), při instalaci navíc dočasně cache. **Mít volných aspoň 15 GB na instalaci, 30 GB na první zkoušky, 60 GB na hodinová videa** (viz kapitola 7). |
| Internet | Při instalaci (GitHub, PyPI, Hugging Face), při stahování videí z YouTube a při překladu přes Codex. Samotný dabing modely stahovat nemusí. |
| Přístup ke GitHubu | Repozitář je soukromý. Potřebuješ pozvánku od vlastníka a svůj účet na GitHubu. |
| Účet ChatGPT | Pro překlad Codexem (předplatné ChatGPT, ne placené API). Bez něj použiješ lokální překladač (Ollama nebo LM Studio). |
| Python, Git | Python 3.12 si `uv` stáhne samo, nic neinstaluj. Git dodají Command Line Tools. |

Čas instalace: celá instalace trvá zhruba 30 až 90 minut (odhad, převážně stahování: kroky 1 až 3
asi 10 až 20 minut, krok 5 několik minut, krok 6 podle rychlosti internetu). Mac nech v zásuvce a
neuspávej ho.

Čas a limit: dabing trvá zhruba 3 až 8násobek délky videa (viz kapitola 7). Překlad Codexem čerpá
limit tvého předplatného ChatGPT.

## 2. Ruční postup (krok za krokem)

Kroky 2 a 4 obsahují heslo nebo přihlášení. Dělej je sám v terminálu. AI asistent je za tebe
dělat nemá a ty mu heslo ani přihlašovací kód neposílej.

Slovníček:

- **HF** = Hugging Face, web, odkud se stahují modely (soubory s „mozkem" umělé inteligence).
- **Cache modelů** = složka `~/.cache/huggingface/hub`, kam se modely uloží, aby je lab našel.
- **Prostředí (`.venv`)** = složka s Python knihovnami jen pro tento projekt, nic v systému nemění.
- **Worker** = samostatný proces, který namlouvá hlas; když spadne, lab ho jen restartuje.
- **MPS** = grafický akcelerátor Macu, na kterém se počítá hlas.
- **PATH** = seznam složek, kde Terminál hledá příkazy.

### Krok 1: Command Line Tools (obsahují git)

```bash
xcode-select --install
```

Otevře se okno, potvrď instalaci a počkej na konec. Kontrola: `xcode-select -p` vypíše cestu a
`git --version` vypíše verzi. Když už je nainstalováno, příkaz to jen oznámí.

### Krok 2: Homebrew

Musíš být správce Macu. Instalátor si vyžádá heslo k Macu, zadej ho sám: při psaní se nezobrazuje
(ani tečky), napiš ho a stiskni Enter. Pak se zeptá „Press RETURN/ENTER to continue": stiskni Enter.
Trvá několik minut. Vlož jen tento blok a počkej, až skončí:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Teprve potom vlož další blok (nastaví PATH, aby Terminál příkaz `brew` našel):

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Kontrola: `brew --version`.

### Krok 3: Nástroje

```bash
brew install ffmpeg yt-dlp uv gh
```

- `yt-dlp` z Homebrew přitáhne i `deno`, což je JavaScript runtime, který YouTube vyžaduje.
  yt-dlp musí být **příkaz v PATH** (Python balíček v projektu není).
- `gh` je jen kvůli soukromému repozitáři.
- Volitelně, jen pro lokální překlad: `brew install ollama` nebo `brew install --cask lm-studio` (`instalovat.sh` je neinstaluje).

Kontrola: `ffmpeg -version | head -1`, `ffprobe -version | head -1`, `yt-dlp --version`,
`deno --version`, `uv --version`.

### Krok 4: Stažení repozitáře

**Pozvánku přijmi:** v e-mailu od GitHubu klikni na View invitation a Accept (nebo se přihlas na
github.com a otevři Notifications). Jinak repozitář uvidíš jako „Not found".

Přihlášení k GitHubu dělej sám. Vlož jen tento blok a odpovídej na otázky: vyber GitHub.com, HTTPS,
na dotaz o Git credentials odpověz Y, pak Login with a web browser; kód z terminálu opiš do
prohlížeče.

```bash
gh auth login
```

Po dokončení vlož další blok. Místo `<VLASTNIK>` dosaď vlastníka repozitáře (je v odkazu z pozvánky):

```bash
gh auth status
cd ~
gh repo clone <VLASTNIK>/dabing-lab
cd dabing-lab
```

Od tohoto kroku je k dispozici i skill pro Claude Code (viz úvod).

Od teď spouštěj všechny příkazy v této složce (`~/dabing-lab`, nebo kam jsi repozitář naklonoval). Složku pak
**nepřesouvej**: prostředí v ní (`.venv`) má zapsané absolutní cesty. Kdyby ses přesunul,
smaž obě složky `.venv` a udělej krok 5 znovu.

### Krok 5: Python prostředí

Dvě prostředí: hlavní (`.venv`) a samostatné pro hlas (`worker/omnivoice/.venv`). Hlas běží
odděleně, protože chyba paměti GPU shodí celý proces a lab ho pak jen restartuje.

```bash
if [ -f uv.lock ]; then uv sync --frozen; else uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml; fi
(cd worker/omnivoice && uv sync)
```

Příkaz jde bezpečně zopakovat (oprava, aktualizace): `--allow-existing` zachová existující `.venv`
a doinstaluje, co chybí.

- Python 3.12 `uv` stáhne samo (desítky MB). Instalace hlavního prostředí zabere několik minut:
  stahuje torch a další balíčky a klonuje z GitHubu knihovnu `transformers` v pevné verzi
  (zhruba 0,5 GB, potřebuje to git a internet). Čas neměřen.
- Důvod podmínky v prvním řádku: v repozitáři zatím nemusí být soubor `uv.lock` a samotné `uv sync`
  na čistém klonu může selhat (balíček demucs vyžaduje starý torch pro Intel Mac). Když soubor
  `uv.lock` v repozitáři je, použije se `uv sync --frozen` s přesnými verzemi. Hlasový worker má
  prosté `uv sync`: zamčené verze respektuje a po změně `pyproject.toml` je přegeneruje.
- Kontrola: `.venv/bin/python --version` (Python 3.12.x) a
  `worker/omnivoice/.venv/bin/python --version` (Python 3.12.x).
- Nespouštěj `uv sync --upgrade` ani neaktualizuj `openai-codex`: Codex je připnutý na verzi
  0.154.0 včetně přibaleného programu a novější verze nebyly s labem zkoušeny.

### Krok 6: Modely

Lab běží offline (`HF_HUB_OFFLINE=1`), takže modely musí být před prvním během stažené.
Stáhne je skript a **u každého se zeptá, jestli ho chceš**:

První příkaz jen vypíše tabulku a stav a nic nestahuje:

```bash
./scripts/stahni_modely.sh --list
```

Druhý se u každého modelu zeptá [y/N] (odpověz `y` nebo `n` a Enter):

```bash
./scripts/stahni_modely.sh
```

Skript před každým stažením ukáže zdroj, velikost a licenci. Co už je stažené, přeskočí.
Celkem je to zhruba **6,5 GB** (povinné modely 6,35 GB; BandIt a Silero jsou volitelné).
Detaily a ruční příkazy jsou v kapitole 3.

### Krok 7: Kontrola instalace

```bash
./spustit.sh --kontrola
```

Čekáš `Vše v pořádku.` (nebo jen upozornění na volitelné modely BandIt a Silero). Chybějící
povinný model je chyba a `./spustit.sh` bez něj nenastartuje. Podrobný žebřík zkoušek je v
kapitole 5.

### Krok 8: Spuštění a přihlášení ke Codexu

```bash
./spustit.sh
```

Otevře se prohlížeč na `http://127.0.0.1:7872`. Okno Terminálu nech otevřené, dokud lab používáš
(`Ctrl+C` ho ukončí). Další příkazy (kontrola účtu, výběr modelů) spouštěj v **novém okně Terminálu**
(Cmd+N) a nejdřív v něm napiš `cd ~/dabing-lab` (nové okno začíná v domovské složce, kde
`./spustit.sh` neexistuje). Spuštění vždy takto: `cd ~/dabing-lab && ./spustit.sh`. Překlad Codexem
vyžaduje přihlášení v samotném labu, viz kapitola 4.

## 3. Modely: zdroje, velikosti, licence

Velikosti jsou v dekadických jednotkách (1 GB = 10^9 B). Žádný z modelů není na Hugging Face
„gated", účet ani token na HF nepotřebuješ.

| Klíč | Model a účel | Zdroj | Velikost | Licence | Povinný |
|---|---|---|---|---|---|
| `parakeet` | Parakeet TDT 0.6B v3 (MLX), přepis řeči | HF `mlx-community/parakeet-tdt-0.6b-v3` | 2,51 GB | CC-BY-4.0 (uvést autora, NVIDIA) | ano |
| `nemotron` | Nemotron-3-Diarization, rozlišení mluvčích | HF `nvidia/Nemotron-3-Diarization` (jen 3 soubory) | 397 MB (celé repo 0,71 GB, to se nestahuje) | OpenMDW-1.1 (karta: komerční i nekomerční použití) | ano |
| `htdemucs` | HTDemucs, oddělení hlasu od pozadí | HF `adefossez/HTDemucs` | 84 MB | HF repo licenci neuvádí; projekt Demucs je MIT (pro váhy výslovné prohlášení nenalezeno) | ano |
| `omnivoice` | OmniVoice, klonování hlasu | HF `k2-fsa/OmniVoice` (celé repo) | 3,27 GB | váhy **CC-BY-NC (jen nekomerční)**; kód Apache-2.0; `audio_tokenizer/` pod Boson Higgs Audio 2 Community License | ano |
| `ecapa` | ECAPA-VoxCeleb, kontrola podobnosti hlasu | HF `speechbrain/spkrec-ecapa-voxceleb` | 89 MB | Apache-2.0 (podle karty; data VoxCeleb mají vlastní podmínky, nezkoumáno) | prakticky ano |
| `bandit` | BandIt Plus, dialog vs. hudba a efekty | GitHub `ZFTurbo/Music-Source-Separation-Training`, release `v.1.0.3` | 148,9 MB | váhy odvozené od BandIt (CC-BY-NC-4.0), licence tohoto souboru neověřena, bráno jako nekomerční | ne |
| `silero` | Silero VAD, detekce řeči | GitHub `snakers4/silero-vad`, tag `v6.2.3` | 2,3 MB | MIT | ne |

Bez BandItu pipeline doběhne, ale jásot a smích přes řeč zmizí spolu s hlasem. Bez Silera se
podklad řídí jen časy slov (kvalita klesne, nespadne).

### Použití skriptu

- `./scripts/stahni_modely.sh`: všechny modely, u každého [y/N] (`d` ukáže soubory a velikost).
- `./scripts/stahni_modely.sh parakeet ecapa`: jen vybrané.
- `./scripts/stahni_modely.sh --list`: nic nestahuje.

Přepínač `--ano <model>` stáhne jmenované modely bez ptaní. Je pro AI asistenta, který se už
předtím zeptal tebe. Skript bez interaktivního terminálu a bez `--ano` nestáhne nic.

BandIt se po stažení ověří podle SHA-256 z `dabing/config.py`
(`c48284779f7d1258a6527d3aaa18a532d45c1f506e2dcc25d5ab179a8c5e2573`), Silero podle
`e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720`. Při nesouhlasu se soubor smaže.

### Ruční příkazy (totéž co skript)

Spouštěj z kořene repozitáře, **bez** nastaveného `HF_HUB_OFFLINE`. Před stažením lze přidat
`--dry-run` (ukáže soubory a velikost, nic nestáhne).

```bash
unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE
HF=.venv/bin/hf

$HF download mlx-community/parakeet-tdt-0.6b-v3 config.json model.safetensors
$HF download nvidia/Nemotron-3-Diarization config.json model.safetensors processor_config.json
$HF download adefossez/HTDemucs htdemucs.yaml 955717e8.safetensors
$HF download k2-fsa/OmniVoice
$HF download speechbrain/spkrec-ecapa-voxceleb \
  hyperparams.yaml embedding_model.ckpt mean_var_norm_emb.ckpt classifier.ckpt label_encoder.txt
```

BandIt Plus (volitelné):

```bash
mkdir -p modely/bandit_plus
curl -fL -o modely/bandit_plus/model_bandit_plus_dnr_sdr_11.47.chpt \
  "https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v.1.0.3/model_bandit_plus_dnr_sdr_11.47.chpt"
echo "c48284779f7d1258a6527d3aaa18a532d45c1f506e2dcc25d5ab179a8c5e2573  modely/bandit_plus/model_bandit_plus_dnr_sdr_11.47.chpt" | shasum -a 256 -c -
```

Silero VAD (volitelné; cestu očekává `dabing/config.py`):

```bash
D=~/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data
mkdir -p "$D"
curl -fL -o "$D/silero_vad.jit" \
  "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.jit"
echo "e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720  $D/silero_vad.jit" | shasum -a 256 -c -
```

Pasti:

- **Nepoužívej `--revision <sha>` ani `--local-dir`.** Lab čte jen standardní cache, a s revizí
  zadanou hashem se nezapíše `refs/main`, takže offline načtení selže.
- Stažení i běh musí používat stejnou cache: výchozí je `~/.cache/huggingface/hub`. Když máš
  nastavené `HF_HOME` nebo `HF_HUB_CACHE`, nastav ho stejně i pro lab.
- Z Nemotronu se stahují jen tři soubory. Repo obsahuje i `.nemo` a `.gguf`, ty nestahuj.
- Kdyby stahování z Hugging Face váhalo, zkus `HF_HUB_DISABLE_XET=1` před příkazem.
- Velké soubory v HF cache se jmenují podle svého SHA-256: `~/.cache/huggingface/hub/models--*/blobs/`.
  Například Parakeet `model.safetensors` má jméno
  `05e01c7f396c298cf7d23f61da7b504adeab698f0aaeafd9c82d198625464592`.
- Silero leží ve složce `snakers4_silero-vad_master`, kterou používá i `torch.hub`. Pokud
  Silero VAD používáš v jiných projektech přes `torch.hub.load`, může se s tímhle souborem
  míchat (existující složka se nestahuje znovu). Případná změna cesty je na vlastníkovi repozitáře.

## 4. Překlad: Codex, Ollama nebo LM Studio

V aplikaci je sekce **„Kdo překládá"** se třemi volbami: **Codex**, **Ollama**, **LM Studio**.
U každé aplikace sama zjistí stav, ukáže další krok, nabídne rozbalovací seznam modelů a tlačítko
**„Zapnout server"**. Volba se pamatuje v `nastaveni.json`. Lokální režim (Ollama, LM Studio)
neposílá nic z Macu a kontrola podle snímků je v něm vypnutá.

U Ollamy a LM Studia lab mluví s jejich API na adrese ve volbě **„Adresa API"** (výchozí
`http://127.0.0.1:11434` / `http://127.0.0.1:1234`, uložená zvlášť pro každou službu v `nastaveni.json`,
prázdné pole = výchozí; proměnná prostředí `DABING_OLLAMA_URL` / `DABING_LMSTUDIO_URL` má přednost).
Změna adresy obnoví stav a seznam modelů a platí i pro „Přegenerovat". „Zapnout server" se ukáže jen
u adresy na tomhle Macu; u adresy mimo něj lab varuje, že text videa odchází na ten počítač.

Kvalita překladu: **Codex je nejlepší**, a navíc ověřuje jména na webu. Lokální modely jména na
webu nekontrolují. Který lokální model použít, je volba podle paměti Macu: větší model dá lepší
překlad, ale zabere víc RAM (lokální překladač obecně od 24 GB). Ollamu ani LM Studio ani žádný
model `instalovat.sh` neinstaluje, každý si je stáhne sám.

### Codex (výchozí, nejlepší překlad)

Codex je součástí instalace (balíček `openai-codex` přitáhne vlastní program `codex`). Nic dalšího
neinstaluj (žádné `npm i -g @openai/codex`, žádné `brew install codex`). Tvůj případný vlastní
Codex CLI (`~/.codex`) lab nepoužije a nezmění, běží s oddělenou složkou `codex_home/`.

Přihlášení se dělá **v labu**, ne v terminálu:

1. Spusť `dabing` (nebo `./spustit.sh`), rozbal sekci **„Kdo překládá"** a zvol Codex.
2. Klikni na **„Přihlásit kódem"**: zobrazí se odkaz a kód, otevři odkaz (klidně na telefonu),
   přihlas se účtem ChatGPT a zadej kód. Nebo **„Přihlásit v prohlížeči"** (jen na stejném Macu).
3. Sekce ukáže „Přihlášen účtem ChatGPT … (plán …)".

Pravidla:

- **Nevytvářej symlink `~/.codex/auth.json` do `codex_home/`.** Starší návody to radily, ale je to
  jen volitelné sdílení pro vlastníka repozitáře. Ty se přihlašuješ v labu.
- Soubor `codex_home/auth.json` je tvé přihlášení: nikomu ho neposílej, nekopíruj ani nevkládej do
  chatu. Je v `.gitignore`.
- Lab přijme jen přihlášení účtem **ChatGPT** (předplatné). Přihlášení API klíčem odmítne a
  proměnné jako `OPENAI_API_KEY` při startu zahodí.
- Výchozí model překladu je `gpt-5.6-terra`. Zda ho má každý plán ChatGPT, se z kódu zjistit
  nedá (rozhoduje server). Po přihlášení lze ověřit nabídku modelů:

  ```bash
  .venv/bin/python -c "from dabing.codex_client import Codex; c=Codex(); c.start(); print(c.modely()); c.close()"
  ```

  Když model v seznamu není, překlad skončí chybou „Codex překlad selhal". Kód neupravuj: pošli
  vlastníkovi repozitáře výpis příkazu `c.modely()` (bez e-mailu) a mezitím použij Ollamu nebo LM Studio.
- **Soukromí:** do OpenAI jde text vět, název a popis videa a dotazy na ověření jmen, produktů a
  čísel na webu. **Zvuk nikdy.** Lokální překladače (Ollama, LM Studio) neposílají nic.
- **Kvóta:** překlad čerpá limit předplatného. Z měření autora: 16minutové video zhruba 16 % hlavního
  okna limitu (10 turnů), 54minutové zhruba 21 %. Plán předplatného může limit snižovat.
- Stav účtu (bez tokenů, nespotřebuje kvótu; vypíše e-mail účtu):
  `.venv/bin/python -c "from dabing import ucet; print(ucet.stav())"`

### Ollama (lokálně, nic neodchází z Macu)

1. `brew install ollama`.
2. V aplikaci zvol Ollamu a klikni **„Zapnout server"**. Bez běžícího serveru model stáhnout nejde.
3. Stáhni si model podle paměti svého Macu: `ollama pull <model>` (velikost modelu je u něj na
   <https://ollama.com/library>). Volba je na tobě.
4. Klikni **„Znovu zkontrolovat"** a v rozbalovacím seznamu vyber model. Seznam ukáže všechny
   stažené modely kromě cloudových, ty běží mimo Mac.

### LM Studio (lokálně, nic neodchází z Macu)

1. `brew install --cask lm-studio`, v aplikaci si stáhni model podle paměti svého Macu (potřebuje
   slušnou češtinu).
2. V aplikaci labu zvol LM Studio a klikni **„Zapnout server"** (nebo v LM Studiu Developer,
   Start Server na `http://127.0.0.1:1234`). Pak v rozbalovacím seznamu vyber model.
3. Když model vrací prázdnou odpověď, vypni v něm přemýšlení (thinking/reasoning).
4. Rychlý test: `curl -s http://127.0.0.1:1234/v1/models`. Model zabírá RAM navíc.

## 5. Zkoušky po instalaci (žebřík)

Každý stupeň je levnější než další. Když selže, dál nepokračuj, oprav ho (kapitola 6).
Příkazy spouštěj z kořene repozitáře.

### Stupeň 0: počítač a nástroje (do 1 s)

```bash
uname -m
sw_vers -productVersion
df -h .
command -v ffmpeg ffprobe yt-dlp deno uv git
```

Čekáš `arm64`, verzi macOS 14 nebo vyšší, výpis volného místa (aspoň 15 GB) a cesty ke všem
nástrojům.

### Stupeň 1: prostředí (zhruba 10 s)

```bash
.venv/bin/python -c "import torch, mlx.core as mx; print('MPS', torch.backends.mps.is_available(), mx.default_device())"
.venv/bin/python -c "import importlib.util as u; print('Nemotron v transformers:', u.find_spec('transformers.models.nemotron3_diarization') is not None)"
"$(.venv/bin/python -c 'from codex_cli_bin import bundled_codex_path; print(bundled_codex_path())')" --version
.venv/bin/python -c "import app; print('app OK')"
worker/omnivoice/.venv/bin/python -W ignore -c "import torch, omnivoice, speechbrain; print('worker torch', torch.__version__, 'MPS', torch.backends.mps.is_available())"
```

Čekáš: `MPS True Device(gpu, 0)`, `Nemotron v transformers: True`, `codex-cli 0.154.0`, `app OK`,
`worker torch 2.8.0 MPS True`. Hláška `[ERROR] image_like_kwargs …` při importu transformers je
neškodná.

### Stupeň 2: modely (do 1 s)

```bash
./spustit.sh --kontrola
```

Čekáš `Vše v pořádku.` Upozornění na BandIt nebo Silero nejsou chyby (dabing půjde, jen hůř
zachová jásot a masku). Chybějící povinný model je chyba (skript skončí kódem 1) a vypíše jeho
jméno.

### Stupeň 3: zdraví workeru hlasu (desítky sekund, načte OmniVoice do paměti)

```bash
printf '%s\n' '{"op":"nacti"}' '{"op":"konec"}' | HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  nice -n 19 worker/omnivoice/.venv/bin/python -W ignore worker/omnivoice/omni_worker.py 2>/dev/null
```

Čekané tři řádky:

```
{"ok": true, "pripraven": true, "zarizeni": "mps"}
{"ok": true, "zarizeni": "mps"}
{"ok": true}
```

Prázdný nebo jiný výstup: spusť totéž bez `2>/dev/null` a čti chybu (`LocalEntryNotFoundError` =
chybí model `k2-fsa/OmniVoice`, `ImportError` = nedoběhl krok 5 pro worker). `"zarizeni": "cpu"`
znamená, že MPS není dostupné. Pozn.: tento stupeň vychází ze čtení kódu workeru, na čisté
instalaci nebyl zkoušen.

### Stupeň 4: první skutečný dabing

Použij **vlastní nahrávku** (minuta až dvě, anglicky, jeden mluvčí, jasná řeč, bez hlasité hudby)
nebo video s licencí povolující odvozená díla. Odpadne otázka souhlasu i autorských práv. Natoč
krátké **video** s hlasem (QuickTime Player, Soubor, Nový filmový záznam, 1 až 2 minuty; ne jen
zvuk). Při zvukové nahrávce (`.m4a`) vznikne jen `.cs.wav`, `.cs.srt` a `.podklad.flac` a MP4
chybět bude, to není chyba.

Soubor nahraj do labu přetažením (pole „…nebo vlastní video/zvuk"), nebo v poli „Odkaz na YouTube"
použij cestu: ve Finderu klikni na soubor pravým tlačítkem, podrž Option a zvol „Copy … as Pathname".

1. Spusť `./spustit.sh`, přihlas se ke Codexu (kapitola 4).
2. Nastav „Jazyk videa" Angličtina, „Počet mluvčích" Automaticky, „Jazyk dabingu" Čeština a klikni
   **Spustit**.
3. Průběh je vidět v UI (v rozbalovátku „Log" i řádky s procenty). Zkouška na 1 až 2 minutách
   zabere zhruba 5 až 15 minut (odhad z měření autora, kapitola 7).
4. Úspěch:
   - UI skončí souhrnem „**<název>** – N vět, mluvčích 1." (pro minutu videa zhruba 6 až 12 vět),
   - medián CER (srozumitelnost) v logu je do 5 % (autorovy testy 0,5 až 3 %),
   - řádek „Hlasitost: originál X LUFS, dabing Y LUFS" ukazuje rozdíl do ±1 LU,
   - v `jobs/<ID>/vystup/` jsou u videa čtyři soubory: `<název>.cs.mp4`, `.cs.wav`, `.cs.srt` a
     `.podklad.flac` (u čistě zvukového vstupu bez MP4),
   - MP4 má dvě zvukové stopy (čeština výchozí, originál druhá):
     `ffprobe -v error -show_entries stream=index,codec_type:stream_tags=language,title -of compact "<soubor.mp4>"`,
   - poslechni 20 sekund: hlas je podobný originálu a pod ním není slyšet angličtina.
5. Po zkoušce smaž `jobs/<ID>` (150 až 250 MB). Modely nech.

Volitelně, bez překladu a hlasu (analýza po krocích, odhad 2 až 4 minuty):
`nice -n 19 .venv/bin/python spike/test_kroky.py <soubor> analyzuj`.

## 6. Řešení potíží

Chybu hledej v okně terminálu, kde běží `./spustit.sh`, v rozbalovátku „Log" v UI a pro hlas
v `logy/omnivoice.log`. **Neopakuj slepě**: nejdřív si přečti, co skutečně selhalo.

| Příznak | Příčina | Náprava |
|---|---|---|
| `./spustit.sh` hlásí „Chybí prostředí" nebo „No such file" | Nedoběhl krok 5, složka se přesunula, nebo jsi v nové záložce Terminálu mimo složku projektu | Nejdřív `cd ~/dabing-lab`. Pak zopakuj krok 5. Po přesunu složky nejdřív smaž obě `.venv`. |
| `./spustit.sh` hlásí „chybí povinné modely" | Model není v HF cache | `./scripts/stahni_modely.sh` (kapitola 3). |
| MLX nebo MPS nejdou, `uname -m` není `arm64` | Intel Mac, Terminal pod Rosettou, macOS starší než 14 | Potřebuješ Apple Silicon a macOS 14+. Jinak to nepůjde. |
| `uv sync` selže s hláškou o `demucs` a `torch<2.3` (v kořeni repozitáře) | `uv sync` řeší balíčky univerzálně i pro Intel Mac | Použij příkaz z kroku 5 (`uv venv --python 3.12 --allow-existing` a `uv pip install -r pyproject.toml`). |
| `A virtual environment already exists` | Starší verze příkazu bez `--allow-existing` | Použij příkaz z kroku 5, ten existující `.venv` zachová. |
| Instalace padá na `transformers @ git+…` | Chybí `git` (`xcode-select --install`) nebo je nedostupný GitHub | Nainstaluj git, zkontroluj internet, zopakuj krok 5. |
| `uv` nenajde Python 3.12 | Chyba stahování | Spusť `uv python install 3.12` a zopakuj krok 5. Systémový Python 3.13 projekt neumí. |
| Port 7872 je obsazený | Běží starý lab nebo jiný program | `lsof -nP -iTCP:7872 -sTCP:LISTEN`. Spouštěč to pozná a nabídne otevření už běžícího labu. |
| Model chybí: `Krok … selhal`, `LocalEntryNotFoundError`, „Cannot find … outgoing traffic has been disabled", nebo zavádějící `FileNotFoundError …/config.json` u Parakeetu | Model není v HF cache (běh je offline) | `./spustit.sh --kontrola` ukáže který. Stáhni ho skriptem (kapitola 3). `HF_HUB_OFFLINE` nikdy nevypínej. |
| Model je stažený, ale lab ho nenajde | Stahoval jsi s jiným `HF_HOME` nebo `HF_HUB_CACHE`, nebo s `--revision` či `--local-dir` | Stáhni znovu do standardní cache stejnou proměnnou, s jakou běží lab. |
| Nemotron: chyba o neznámé architektuře nebo konfiguraci | Příliš starý `transformers` | Zopakuj krok 5 (projekt připíná konkrétní commit z gitu). |
| V logu `fbaipublicfiles` nebo `URLError` u Demucs | Demucs bez prefixu `hf://` sahá na internet | V `dabing/config.py` musí zůstat `MODEL_SEPARACE = "hf://htdemucs"`. |
| `BandIt chybí` | Soubor není v `modely/bandit_plus/` | Nefatální. Stáhni `bandit` skriptem. |
| `Soubor modelu BandIt se liší od ověřeného (SHA-256)` | Poškozené stažení | Smaž soubor a stáhni znovu. |
| `Silero VAD chybí … maska podkladu pojede jen podle slov` | Soubor není na místě | Nefatální. Stáhni `silero` skriptem. |
| `yt-dlp skončil chybou … HTTP Error 403` | Starý yt-dlp (YouTube se mění) | `brew upgrade yt-dlp` (u Homebrew nejde `yt-dlp -U`). Ověř `deno --version`. |
| `Sign in to confirm you're not a bot` | YouTube blokuje IP adresu, nejčastěji kvůli VPN | Přepni VPN na jinou zemi, nebo ji vypni, a zkus znovu. |
| `yt-dlp nenačetl video` | Soukromé nebo věkově omezené video, geoblok, „Sign in to confirm you're not a bot" | Lab cookies neumí. Pokud k videu máš právo, stáhni ho jinak a napiš cestu k souboru do pole „Odkaz" (nebo ho nahraj). |
| `loudnorm nevrátil měření`, chyba ffmpegu | Chybí nebo starý ffmpeg | `brew install ffmpeg` (včetně `ffprobe`). |
| Před spuštěním: „Překlad Codexem potřebuje přihlášení" | Nejsi přihlášený | Sekce „Kdo překládá", kapitola 4. Nebo zvol Ollamu či LM Studio. |
| „Codex je přihlášený API klíčem, lab jede jen přes předplatné" | Přihlášení nebylo účtem ChatGPT | V sekci „Kdo překládá" se odhlas a přihlas účtem ChatGPT. |
| `Stav účtu se nepodařilo zjistit …` | Nenaběhl přibalený Codex | Zkus stupeň 1 (`codex-cli 0.154.0`). Zopakuj krok 5. |
| `Codex překlad selhal (failed): …` | Vyčerpaná kvóta, nebo model `gpt-5.6-terra` účet nemá | Počkej na reset limitu, přepni na Ollamu či LM Studio, nebo ověř modely (kapitola 4). |
| `Překlad chybí u vět […]` | Neúplná odpověď | Spusť znovu, hotové kroky se přeskočí. |
| `gh repo clone` hlásí `Could not resolve to a Repository` nebo `404` | Pozvánka není přijatá, špatný `<VLASTNIK>`, nebo jsi v `gh auth login` zvolil jiný účet | Přijmi pozvánku, ověř `gh auth status` (správný účet) a přesné jméno vlastníka z odkazu v pozvánce. |
| Instalace nebo stahování se přerušilo (zavřené okno, vypadlý internet, uspání) | Přerušený krok | Zopakuj stejný příkaz: `uv`, `brew` i stahování modelů naváží a hotové přeskočí. Nic nemaž a nepřeinstalovávej. |
| `No space left on device` | Plný disk | Uvolni místo (`df -h .`), zopakuj krok. Úklid: kapitola 9. Nic nemaž naslepo. |
| `git pull` hlásí, že by přepsal `uv.lock` (untracked working tree files) | Lokálně vzniklý `uv.lock`, který vlastník mezitím přidal do repozitáře | Smaž lokální soubory (`rm -f uv.lock worker/omnivoice/uv.lock`), spusť `git pull` znovu a pak příkazy z kroku 5. |
| `LM Studio neběží …` / prázdná odpověď | Server neběží, nebo model vrací prázdnou odpověď (zapnuté přemýšlení) | Kapitola 4: „Zapnout server", případně vypnout thinking. |
| `Věta N: Worker skončil (nejspíš chyba MPS) … restartuji worker` | Chyba paměti GPU shodila hlasový proces | Ojedinělé je v pořádku. Při opakování (`worker opakovaně spadl`) zavři těžké aplikace (hlavně LM Studio s modelem), restartuj lab a spusť znovu. Hotové kroky se nepočítají znovu. |
| `Worker neodpověděl do 60 s` | Úplně první start nového prostředí (příčina neověřena) | Zkus znovu. |
| `Worker neodpověděl do 300 s` (věta) nebo `do 600 s` (načtení modelu) | Málo paměti nebo uspání Macu | Zavři ostatní aplikace, Mac nech v zásuvce s otevřeným víkem. |
| `BandIt selhal: …` | Chyba GPU nebo NaN | Lab zkusí procesor sám. Ručně: `DABING_DIALOG_CPU=1 ./spustit.sh`. Dabing doběhne, jen zmizí jásot a smích přes řeč. |
| `Mluvčí M1: kotva hlasu mu není dost podobná (0.00 …)` | Může skrývat chybu ECAPA v hlasovém workeru | Zkontroluj `logy/omnivoice.log`, že je v cache `speechbrain/spkrec-ecapa-voxceleb`. Po přesunu složky smaž `worker/omnivoice/ecapa/` (vznikne znovu). |
| `Už běží jiný dabing — počkej, nebo ho zruš.` | Lab zpracovává jednu zakázku najednou | Počkej, nebo klikni Zrušit (zastaví až po aktuálním kroku či větě, může to trvat desítky sekund). |
| Dabing se zasekl, Mac se uspal | Uspání zastaví GPU | Mac v zásuvce, víko otevřené. `./spustit.sh` sám brání uspání (`caffeinate`). |
| Po pádu labu nebo restartu Macu uprostřed dabingu | Běh se přerušil | Spusť znovu se stejným odkazem nebo souborem: zakázka `jobs/<ID>` naváže a hotové kroky přeskočí. |

Hlášky `Loading weights: …` v `logy/omnivoice.log` a `[ERROR] image_like_kwargs …` jsou normální.
Značky v tabulce vět (například „HLAS NESEDÍ — poslechni") jsou upozornění pro tebe, ne chyby běhu.

## 7. Co čekat: čas, disk, kvóta

Čísla jsou z provozu autora na MacBooku Pro M4 Pro (24 GB RAM). Na slabším čipu počítej víc.

- **Čas dabingu:** zhruba 3 až 8násobek délky videa (naměřeno 2,2 až 7,6násobek). Příklady:
  30 s video 157 s; 2,5 min video 5,4 až 9 min; 33 min (jen přegenerování hlasu) 2 h 53 min;
  54 min (701 vět, 8 mluvčích) 6 h 28 min. Hlas OmniVoice běží pomaleji než reálný čas a
  doladění délek vět přidává 40 až 55 % syntéz.
- **Dlouhé běhy:** Mac v zásuvce, víko otevřené, ostatní těžké aplikace zavřené. Jedna zakázka
  najednou. Záložku prohlížeče za běhu nezavírej ani neobnovuj.
- **Disk na zakázku** (výpočet z formátů souborů, neměřeno): zhruba 130 až 200 MB na minutu videa do
  úklidu (8 až 12 GB na hodinu). Po smazání pracovních souborů (nech `vystup/` a `job.json`) zbyde
  zhruba 33 až 68 MB na minutu. Pozor: bez pracovních souborů nejde „Přegenerovat změněné" bez nového
  výpočtu. Ověř `du -sh jobs/<ID>`.
- **RAM:** špička při separaci zhruba 0,23 GB na minutu videa (odhad, 60 min zhruba 15 GB). Při nahrání
  souboru přes prohlížeč se krátce spotřebuje 2 až 3násobek jeho velikosti, u velkých souborů raději
  napiš cestu k souboru do pole „Odkaz".
- **Kvóta Codexu:** viz kapitola 4.

## 8. Provoz a aktualizace

- Spuštění vždy takto: `cd ~/dabing-lab && ./spustit.sh` (cesta podle toho, kam jsi repozitář
  naklonoval). Okno Terminálu nech otevřené, dokud lab používáš (`Ctrl+C` ho ukončí). Další příkazy
  spouštěj v novém okně Terminálu (Cmd+N) a nejdřív v něm napiš `cd ~/dabing-lab`. Volitelně
  proměnné `LAB_NO_OPEN=1` (neotevírat prohlížeč), `LAB_NO_CAFFEINATE=1`, `DABING_DIALOG_CPU=1`.
  Spouštěč nastavuje offline režim modelů a vypíná telemetrii Gradia.
- Odkaz do PATH (volitelně, aby stačilo napsat `dabing`). Vlož oba příkazy, pak otevři nové okno
  Terminálu (nebo `source ~/.zprofile`):

  ```bash
  mkdir -p ~/.local/bin && ln -s "$PWD/spustit.sh" ~/.local/bin/dabing
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
  ```

- Aktualizace kódu: `git pull`, pak znovu příkazy z kroku 5 a `./spustit.sh --kontrola`.
  `yt-dlp` aktualizuj `brew upgrade yt-dlp` (YouTube se mění často). Kdyby `git pull` hlásil, že
  by přepsal `uv.lock`, smaž lokálně vzniklý soubor (`rm -f uv.lock worker/omnivoice/uv.lock`),
  spusť `git pull` znovu a pak příkazy z kroku 5.

## 9. Odinstalace a úklid disku

Nejdřív v labu klikni na „Odhlásit" v sekci „Kdo překládá" (smaže přihlášení z `codex_home/`).

Velikosti zakázek a jejich smazání (za `<ID>` dosaď jméno složky):

```bash
du -sh jobs/*
rm -rf jobs/<ID>
```

Modely z Hugging Face cache (smaž jen ty, které nepoužívají tvé jiné projekty):

```bash
rm -rf ~/.cache/huggingface/hub/models--mlx-community--parakeet-tdt-0.6b-v3
rm -rf ~/.cache/huggingface/hub/models--nvidia--Nemotron-3-Diarization
rm -rf ~/.cache/huggingface/hub/models--adefossez--HTDemucs
rm -rf ~/.cache/huggingface/hub/models--k2-fsa--OmniVoice
rm -rf ~/.cache/huggingface/hub/models--speechbrain--spkrec-ecapa-voxceleb
```

Silero:

```bash
rm -f ~/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.jit
```

`uv cache clean` smaže sdílenou cache uv i pro tvé jiné projekty (jen se znovu stáhnou balíčky);
spusť ho jen pokud chceš uvolnit místo:

```bash
uv cache clean
```

Celý projekt (včetně `.venv`, `modely/` a `codex_home/`) odstraníš smazáním složky `dabing-lab`.
Nástroje z Homebrew (`brew uninstall ffmpeg yt-dlp deno uv gh`) odstraň jen pokud je jinde nepoužíváš.
Python 3.12 od `uv`: `uv python uninstall 3.12` (jen když ho nepotřebují jiné projekty).

## 10. Licence a právo

Toto jsou technická upozornění, ne právní rada.

- **Jen nekomerční použití.** Váhy OmniVoice jsou **CC-BY-NC**, váhy BandIt Plus jsou odvozené od
  CC-BY-NC-4.0 modelu. Dabované video, které monetizuješ (reklamy, sponzoring, prodej), je komerční
  použití a licence ho nepovolují.
- **Klonování hlasu jen se souhlasem.** Lab naklonuje hlas **každého mluvčího** v dabovaném videu.
  Souhlas proto potřebuješ od všech mluvčích (u vlastní nahrávky od sebe a lidí, kteří v ní mluví).
  U cizího videa bez jejich souhlasu dabing nezveřejňuj. Hlas patří k osobnostním právům.
- **Autorská práva k videu.** Stažení a dabing cizího videa a hlavně jeho zveřejnění je autorskoprávní
  a licenční otázka a spadá pod podmínky YouTube. Bezpečné je vlastní video, video s licencí povolující
  odvozená díla, nebo video se svolením autora. Veřejné nahrání dabingu cizího videa bez svolení
  nedoporučujeme (autorská práva, Content ID).
- **Označ umělý hlas.** YouTube u realistických syntetických hlasů vyžaduje přiznání, mohou platit i
  evropská pravidla (AI Act, čl. 50). Zkontroluj aktuální znění. Dabing označ jako vytvořený umělou
  inteligencí.
- **Uvedení autorů.** Parakeet TDT v3 (CC-BY-4.0, NVIDIA), OmniVoice (CC-BY-NC, k2-fsa) a BandIt
  Plus (CC-BY-NC-4.0) vyžadují uvedení původu. Podsložka `audio_tokenizer/` modelu OmniVoice je pod
  Boson Higgs Audio 2 Community License, která při šíření modelu nebo produktu s ním vyžaduje uvedení
  „Built with Higgs Materials licensed from Boson AI USA, Inc." (plné znění je v souboru LICENSE na
  kartě modelu). Při sdílení výsledků nebo modelů přečti podmínky na kartě každého modelu.
- Modely a jejich licence se mohou měnit. Aktuální stav je vždy na kartě modelu na Hugging Face.
- Nikdy nevkládej do gitu `modely/`, `jobs/`, `codex_home/auth.json` ani média. `.gitignore` je vylučuje.
