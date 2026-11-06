---
name: dabing-lab
description: Instalace, spuštění, aktualizace a řešení potíží Dabing labu (místní Gradio aplikace pro dabing videí z YouTube nebo souborů do češtiny a dalších jazyků, Apple Silicon Mac, Parakeet, Demucs, OmniVoice, Codex, Ollama nebo LM Studio) a vedení uživatele při dabingu videa. Použij, když uživatel chce dabing-lab nainstalovat, nastavit, spustit, aktualizovat, opravit nebo s ním nadabovat video, nebo když chce nainstalovat tento repozitář na čistém Macu.
---

# Dabing lab: playbook pro AI agenta

Pomáháš uživateli (často nezkušenému) nainstalovat a používat Dabing lab na jeho Macu.
Komunikuj česky, krátce a po jednom kroku. Podrobný návod s tabulkami je v
`docs/INSTALACE.md` (kapitoly jsou číslované, odkazuji na ně níže). Tento skill pokrývá klíčové
kroky; úplný návod a tabulka potíží jsou v `docs/INSTALACE.md`. Skill je součástí soukromého
repozitáře, takže se v Claude Code načte až po naklonování (krok 4 níže). Do té doby uživatele
nasměruj na `docs/chatgpt-instrukce.md` (viz krok 4).

## 0. Pravidla, která neporušuješ

1. **Nikdy nečti, nevypisuj, nekopíruj ani neposílej** soubory s přihlášením a tajemstvími:
   `codex_home/auth.json`, `~/.codex/auth.json`, tokeny, API klíče, cookies. Nevytvářej symlink
   `~/.codex/auth.json` do `codex_home/` (to je jen pro vlastníka repozitáře). Uživatele
   nežádej, aby ti heslo, token nebo přihlašovací kód vložil do chatu.
2. **Před každým velkým stažením se zeptej** a ukaž repo, velikost a licenci. Modely se
   stahují po jednom a jen po výslovném „ano" k tomu modelu (viz část 4).
3. **Žádné `sudo`**, dokud uživatel výslovně nesouhlasí. Heslo k Macu (instalátor Homebrew)
   a přihlášení k GitHubu zadává uživatel sám. Tyto příkazy musí uživatel spustit ve vlastním
   okně aplikace Terminál (Terminal.app), ne přes `!` a ne přes tvůj Bash nástroj, protože
   potřebují interaktivní vstup (heslo, volby, otázky [y/N]): instalace Homebrew,
   `gh auth login`, `./scripts/stahni_modely.sh`. Ty počkej, až napíše, že doběhly, a ověř
   výsledek (`brew --version`, `gh auth status`, `./scripts/stahni_modely.sh --list`).
4. **Nic nespouštěj naslepo opakovaně.** Když krok selže: přečti chybu a log, najdi příčinu
   (tabulka v části 9 nebo `docs/INSTALACE.md` kapitola 6), oprav ji, pak zopakuj jednou.
5. Nemaž data uživatele (`jobs/`, HF cache, `modely/`) bez jeho souhlasu. Nikdy nespouštěj
   `git push` ani nic nezveřejňuj.
6. Neměň `HF_HUB_OFFLINE`: lab běží offline a modely musí být předem v HF cache.
7. Neupgraduj závislosti (`uv sync --upgrade`, `openai-codex`): Codex je připnutý na 0.154.0.

## 1. Co je Dabing lab

Webové UI na `http://127.0.0.1:7872`. Pipeline: yt-dlp (stažení), Demucs (hlas vs. pozadí),
Parakeet TDT v3 (přepis, MLX), Nemotron (mluvčí), Silero VAD, BandIt Plus (jásot a efekty),
překlad přes Codex (předplatné ChatGPT), nebo lokálně Ollama či LM Studio, OmniVoice (klon hlasu,
vlastní venv, MPS), ECAPA (kontrola hlasu), mix a export (MP4, WAV, SRT, podklad FLAC).
Přepis, mluvčí a hlas běží vždy lokálně. Do OpenAI jde jen text vět, název a popis videa a
webové dotazy na jména. Zvuk nikdy.

## 1b. Nejdřív instalovat.sh

Instalaci dělá jeden skript: v naklonovaném repozitáři `./instalovat.sh` (soukromý repozitář:
`gh repo clone christovnino-a11y/dabing-lab ~/dabing-lab`; po zveřejnění i
`curl -fsSL https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/instalovat.sh | bash`).
Je idempotentní a slouží i jako aktualizace. Postup:

1. Spusť `./instalovat.sh --zkouska` (nic nemění) a ukaž uživateli, co by se stalo.
2. Zeptej se na souhlas se stažením (zhruba 10 GB; modely 6,5 GB, váhy OmniVoice jsou CC-BY-NC,
   jen nekomerčně). Skript se na modely ptá sám; bez terminálu se bez `--ano` nic nestahuje.
3. Skript potřebuje terminál (heslo k Macu pro Homebrew, otázky): nech ho spustit uživatele
   v okně Terminálu. Ty ho s `--ano` spouštěj jen po výslovném „ano" (`--bez-modelu`, když
   modely nechce).
4. Ruční kroky níže (části 2 a 3) použij, když skript selže nebo když něco opravuješ.

## 2. Preflight (než cokoli instaluješ)

Spusť a vyhodnoť:

```bash
uname -m
sw_vers -productVersion
sysctl -n hw.memsize | awk '{printf "%.0f GB RAM\n", $1/1073741824}'
df -h ~ | tail -1
command -v brew git ffmpeg ffprobe yt-dlp deno uv gh
```

Čekáš `arm64`, macOS 14 nebo víc, paměť, volné místo a cesty k nástrojům.

Požadavky: Apple Silicon (Intel a Rosetta nepůjdou), macOS 14+, RAM aspoň 16 GB (24 GB
pohodlně, 8 GB nepoužitelné; lokální překladač od 24 GB), **volných 15 GB na instalaci**
(instalace zabere ~10 GB), 30 GB na první zkoušky, 60 GB na hodinová videa.
Když něco nesedí, řekni to hned a nepokračuj bez souhlasu. Zjisti také, zda uživatel
má účet ChatGPT (pro překlad Codexem) a přijatou pozvánku k soukromému repozitáři.

**Souhlas se stažením:** před krokem 1 řekni uživateli, že instalace stáhne celkem zhruba 10 GB
(nástroje 0,7 GB, Python prostředí 2,6 GB, modely 6,5 GB) a trvá zhruba 30 až 90 minut, a počkej
na jeho výslovné „ano". Před krokem 3 a před krokem 5 znovu řekni velikost (u kroku 5: „teď se
stáhne zhruba 2,6 GB, torch a další balíčky, plus git klon transformers zhruba 0,5 GB") a počkej
na „ano". Modely viz část 4.

## 3. Instalace krok za krokem (po každém kroku ověř)

Dělej v tomto pořadí a přeskoč, co už je hotové.

1. **Command Line Tools:** `xcode-select --install` (okno potvrdí uživatel). Ověření:
   `xcode-select -p` a `git --version`.
2. **Homebrew** (uživatel musí být správce Macu a zadá heslo sám v okně Terminálu; heslo se při
   psaní nezobrazuje a instalátor chce „Press RETURN/ENTER to continue"). Nejdřív samostatně
   `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
   a až po jeho dokončení `echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile`
   a `eval "$(/opt/homebrew/bin/brew shellenv)"`. Ověření: `brew --version`.
3. **Nástroje:** `brew install ffmpeg yt-dlp uv gh`. (`yt-dlp` přitáhne `deno`, který YouTube
   potřebuje; yt-dlp musí být příkaz v PATH.) Pro lokální překlad volitelně
   `brew install ollama` nebo `brew install --cask lm-studio`. Ověření: `ffmpeg -version | head -1`, `yt-dlp --version`,
   `deno --version`, `uv --version`.
4. **Repozitář** (soukromý, uživatel musí mít přijatou pozvánku: v e-mailu od GitHubu View
   invitation a Accept; přihlášení dělá sám v Terminálu): nejdřív samostatně `gh auth login`
   (GitHub.com, HTTPS, na Git credentials Y, Login with a web browser), pak `gh auth status`, pak
   `cd ~ && gh repo clone <VLASTNIK>/dabing-lab && cd dabing-lab`. `<VLASTNIK>` zjisti od
   uživatele (je v odkazu z pozvánky). Chyba `Could not resolve to a Repository` nebo `404`
   znamená nepřijatou pozvánku, špatného vlastníka nebo jiný účet v `gh`. Složku pak nepřesouvej.
   Pokud už v repozitáři jsi, tento krok přeskoč. Když uživatel ještě repozitář nemá, tento skill
   se mu nenačetl: pomůže mu `docs/chatgpt-instrukce.md` (na github.com otevřít soubor, Raw,
   zkopírovat celý text do ChatGPT nebo Claude v prohlížeči) a skill pro Claude Code poběží až
   po naklonování (`cd ~/dabing-lab && claude`).
5. **Python prostředí** (v kořeni repozitáře; Python 3.12 si `uv` stáhne samo, instalace
   klonuje i `transformers` z gitu, ~0,5 GB, trvá několik minut):
   ```bash
   if [ -f uv.lock ]; then uv sync --frozen; else uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml; fi
   (cd worker/omnivoice && uv sync)
   ```
   Pozor: holé `uv sync` v kořeni na čistém klonu bez `uv.lock` může selhat (demucs vyžaduje pro
   Intel Mac starý torch), proto ta podmínka; `--allow-existing` dělá příkaz bezpečně opakovatelný.
   Ověření: `.venv/bin/python --version` a
   `worker/omnivoice/.venv/bin/python --version` (obojí 3.12.x).
6. **Modely** (část 4 níže): `./scripts/stahni_modely.sh --list`, pak po
   souhlasu stažení.
7. **Kontrola:** `./spustit.sh --kontrola` musí skončit „Vše v pořádku." (upozornění na
   volitelný BandIt a Silero nejsou chyba; chybějící povinný model je chyba a výstupní kód 1).
8. **Spuštění a přihlášení Codexu** (část 5 níže).
9. **Zkouška** (část 6 níže).

## 4. Velká stažení: modely (vždy se ptej)

Zobraz uživateli tabulku a zeptej se, které modely chce. Velikosti jsou dekadické.
Žádný model není gated (HF účet ani token netřeba).

| Klíč | Zdroj | Velikost | Licence | Povinný |
|---|---|---|---|---|
| `parakeet` | HF `mlx-community/parakeet-tdt-0.6b-v3` | 2,51 GB | CC-BY-4.0 (uvést autora) | ano |
| `nemotron` | HF `nvidia/Nemotron-3-Diarization` (jen 3 soubory, celé repo 0,71 GB se nestahuje) | 397 MB | OpenMDW-1.1 | ano |
| `htdemucs` | HF `adefossez/HTDemucs` | 84 MB | HF licenci neuvádí, Demucs je MIT | ano |
| `omnivoice` | HF `k2-fsa/OmniVoice` (celé repo) | 3,27 GB | váhy **CC-BY-NC (jen nekomerčně)**, kód Apache-2.0 | ano |
| `ecapa` | HF `speechbrain/spkrec-ecapa-voxceleb` | 89 MB | Apache-2.0 | prakticky ano |
| `bandit` | GitHub ZFTurbo/Music-Source-Separation-Training, release `v.1.0.3` | 148,9 MB | odvozeno od CC-BY-NC-4.0, nekomerčně | ne |
| `silero` | GitHub snakers4/silero-vad, tag `v6.2.3` | 2,3 MB | MIT | ne |

Celkem zhruba 6,5 GB. Bez BandItu zmizí jásot a smích přes řeč spolu s hlasem, bez Silera se
podklad řídí jen časy slov (dabing doběhne).

Jak stahovat:

- Nejjednodušší: řekni uživateli, ať `./scripts/stahni_modely.sh` spustí v samostatném okně
  aplikace Terminál (Terminal.app), ne přes `!` ani přes tvůj Bash nástroj: tam nejde odpovídat
  na otázky [y/N]. Skript se u každého modelu zeptá [y/N], přeskočí hotové a ověří SHA-256.
- Když stahuješ ty (jen po výslovném souhlasu): **po výslovném souhlasu uživatele s konkrétním modelem** spusť
  `./scripts/stahni_modely.sh --ano <klíč> [<klíč> ...]` (jen jmenované modely). Bez `--ano`
  a bez terminálu skript nestáhne nic. Nikdy nepoužívej `--ano` pro modely, na které se
  uživatel nezeptal.
- Ruční alternativa (bez `HF_HUB_OFFLINE`): `.venv/bin/hf download <repo> [soubory]`; přesné
  příkazy jsou v `docs/INSTALACE.md` kapitola 3. **Nepoužívej `--revision` ani `--local-dir`**
  (lab čte standardní cache s `refs/main`). Cache je `~/.cache/huggingface/hub` (nebo podle
  `HF_HOME` / `HF_HUB_CACHE`, stejně pro stažení i běh).
- Když stahování váhá: `HF_HUB_DISABLE_XET=1` před příkazem.

## 5. Codex: přihlášení v UI (nikdy ne přes soubor)

1. Spusť lab na pozadí (`./spustit.sh`, nebo `LAB_NO_OPEN=1 ./spustit.sh` když prohlížeč
   neotevíráš) a řekni uživateli adresu `http://127.0.0.1:7872`.
2. Uživatel v prohlížeči rozbalí sekci **„Kdo překládá"**, zvolí Codex a klikne **„Přihlásit
   kódem"** (odkaz a kód, funguje i z telefonu) nebo **„Přihlásit v prohlížeči"** (jen na
   tomto Macu). Přihlašuje se účtem ChatGPT, **ne API klíčem** (lab ho odmítne).
3. Ty přihlášení nedělej, nezadávej kód za uživatele a nečti `auth.json`. Ověření stavu bez
   tokenů a bez e-mailu (nespotřebuje kvótu):
   `.venv/bin/python -c "from dabing import ucet; s=ucet.stav(); print('přihlášen:', s.prihlasen, 'ověřeno:', s.overeno)"`.
4. Výchozí model překladu je `gpt-5.6-terra`; zda ho má plán uživatele, nelze z kódu zjistit.
   Ověření nabídky po přihlášení:
   `.venv/bin/python -c "from dabing.codex_client import Codex; c=Codex(); c.start(); print(c.modely()); c.close()"`.
   Když model v nabídce není, překlad skončí „Codex překlad selhal". Kód v repozitáři neupravuj:
   řekni uživateli, ať vlastníkovi repozitáře pošle výpis příkazu `c.modely()` (bez e-mailu), a
   mezitím použij Ollamu nebo LM Studio.
5. Uživatel bez ChatGPT: lokální překladač v sekci „Kdo překládá" (Ollama: `brew install ollama`,
   „Zapnout server", pak `ollama pull <model>` a „Znovu zkontrolovat"; LM Studio: model stáhne
   v aplikaci, pak „Zapnout server"). Model si
   vybere sám podle paměti svého Macu, žádný nedoporučuj. Když vrací prázdnou odpověď, vypnout v něm
   přemýšlení. Lokální režim nic neposílá ven, jména neověřuje a kontrola podle snímků je vypnutá;
   Codex překládá nejlépe. Od 24 GB RAM.
6. Řekni mu o kvótě (překlad čerpá limit předplatného; autorova měření: 16 min videa zhruba
   16 %, 54 min zhruba 21 %) a o soukromí (do OpenAI jde text vět, název a popis, dotazy na jména,
   nikdy zvuk).

## 6. Žebřík zkoušek (jakmile stupeň selže, dál nepokračuj)

Z kořene repozitáře. Podrobnosti `docs/INSTALACE.md` kapitola 5.

| Stupeň | Příkaz | Čekáš |
|---|---|---|
| 0 počítač | `uname -m; sw_vers -productVersion; df -h .; command -v ffmpeg ffprobe yt-dlp deno uv git` | arm64, 14+, nástroje nalezeny |
| 1 prostředí | `.venv/bin/python -c "import torch, mlx.core as mx; print('MPS', torch.backends.mps.is_available(), mx.default_device())"` | `MPS True Device(gpu, 0)` |
| 1 | `.venv/bin/python -c "import importlib.util as u; print(u.find_spec('transformers.models.nemotron3_diarization') is not None)"` | `True` |
| 1 | `.venv/bin/python -c "import app; print('app OK')"` | `app OK` |
| 1 | `worker/omnivoice/.venv/bin/python -W ignore -c "import torch, omnivoice, speechbrain; print(torch.__version__, torch.backends.mps.is_available())"` | `2.8.0 True` |
| 2 modely | `./spustit.sh --kontrola` | `Vše v pořádku.` |
| 3 hlasový worker (načte OmniVoice, desítky sekund) | `printf '%s\n' '{"op":"nacti"}' '{"op":"konec"}' \| HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 nice -n 19 worker/omnivoice/.venv/bin/python -W ignore worker/omnivoice/omni_worker.py 2>/dev/null` | tři řádky: `{"ok": true, "pripraven": true, "zarizeni": "mps"}`, `{"ok": true, "zarizeni": "mps"}`, `{"ok": true}` |
| 4 první dabing | v UI, vlastní nahrávka 1 až 2 min | viz níže |

Stupeň 3 vychází ze čtení kódu workeru, na čisté instalaci nebyl zkoušen. Při prázdném výstupu
spusť bez `2>/dev/null` a čti chybu.

**Stupeň 4:** použij vlastní nahrávku (minuta až dvě, anglicky, jeden mluvčí, jasná řeč) nebo
video s licencí povolující odvozená díla, ať odpadne souhlas i autorská práva. Natoč krátké
**video** s hlasem (QuickTime Player, Soubor, Nový filmový záznam, 1 až 2 minuty; ne jen zvuk):
u zvukové nahrávky (`.m4a`) vznikne jen `.cs.wav`, `.cs.srt` a `.podklad.flac`, MP4 chybět bude a
není to chyba. Soubor nahraj do UI přetažením, nebo cestu do pole „Odkaz na YouTube" (Finder:
pravé tlačítko na soubor, podržet Option, „Copy … as Pathname"). V UI: Jazyk videa
Angličtina, Počet mluvčích Automaticky, Jazyk dabingu Čeština, **Spustit**. Čas zhruba 5 až
15 minut (odhad z měření autora). Úspěch: souhrn „N vět, mluvčích 1", medián CER do 5 %,
rozdíl hlasitosti do ±1 LU, v `jobs/<ID>/vystup/` u videa čtyři soubory (`.cs.mp4`, `.cs.wav`,
`.cs.srt`, `.podklad.flac`), MP4 se dvěma zvukovými stopami
(`ffprobe -v error -show_entries stream=index,codec_type:stream_tags=language,title -of compact "<mp4>"`),
a uživatel si poslechne 20 s. Po zkoušce navrhni smazat `jobs/<ID>` (150 až 250 MB), modely nech.

## 7. Co čekat

Dabing trvá zhruba 3 až 8násobek délky videa (naměřeno 2,2 až 7,6násobek na M4 Pro 24 GB).
Příklady: 30 s video 157 s, 54 min video 6 h 28 min. Mac musí být v zásuvce s otevřeným víkem
(`./spustit.sh` sám brání uspání). Disk: zhruba 130 až 200 MB na minutu videa do úklidu
(8 až 12 GB na hodinu), po úklidu pracovních souborů 33 až 68 MB na minutu (výpočet z formátů
souborů, neměřeno). Jedna zakázka najednou, záložku za běhu nezavírat.

## 8. Normální použití a údržba

- Spuštění: `cd ~/dabing-lab && ./spustit.sh` (cesta podle toho, kam je repozitář naklonovaný;
  `LAB_NO_OPEN=1` neotevře prohlížeč, `DABING_DIALOG_CPU=1` počítá BandIt na procesoru).
  Okno Terminálu zůstává otevřené, ukončení `Ctrl+C`; další příkazy v novém okně (Cmd+N) po `cd`.
- Dabing: vlož odkaz na YouTube, nebo nahraj soubor či napiš jeho cestu do pole „Odkaz"; zvol
  režim, jazyky a počet mluvčích; **Spustit**. Výsledek a soubory jsou v UI i v `jobs/<ID>/vystup/`.
  Překlad v tabulce lze upravit a dát „Přegenerovat změněné".
- Po pádu: znovu **Spustit** se stejným odkazem nebo souborem, hotové kroky se přeskočí.
- Aktualizace: `git pull`, znovu příkazy z kroku 5 (část 3, jdou bezpečně opakovat),
  `./spustit.sh --kontrola`; `brew upgrade yt-dlp`; nebo prostě znovu `./instalovat.sh`. Když `git pull` hlásí, že by přepsal `uv.lock`,
  smaž lokálně vzniklý soubor (`rm -f uv.lock worker/omnivoice/uv.lock`), spusť `git pull` znovu a
  pak příkazy z kroku 5.
- Odinstalace a úklid disku: `docs/INSTALACE.md` kapitola 9. Nejdřív „Odhlásit" v sekci Kdo překládá.

## 9. Když něco selže (přečti log, neopakuj slepě)

Kde hledat: výstup terminálu s `./spustit.sh`, rozbalovátko „Log" v UI, `logy/omnivoice.log`.

| Příznak | Příčina a náprava |
|---|---|
| `./spustit.sh` hlásí chybějící prostředí | Zopakuj krok 5 z části 3. Po přesunu složky smaž obě `.venv` a udělej krok 5 z části 3 znovu. |
| `uname -m` není `arm64`, MPS ani MLX nejdou | Intel Mac, Rosetta nebo macOS starší než 14: nepůjde. Řekni to uživateli. |
| `uv sync` selže na `demucs` / `torch<2.3` (v kořeni) | Použij `uv venv --python 3.12 --allow-existing` a `uv pip install -r pyproject.toml` (krok 5 z části 3). |
| `A virtual environment already exists` | Příkaz bez `--allow-existing`; použij příkaz z kroku 5 (části 3). |
| `uv` nenajde Python 3.12 | `uv python install 3.12`, krok 5 znovu. Systémový 3.13 projekt neumí. |
| Model stažený, ale lab ho nenajde | Jiná cache (`HF_HOME` / `HF_HUB_CACHE`), nebo `--revision` / `--local-dir`: stáhnout znovu do standardní cache stejnou proměnnou, s jakou běží lab. |
| `loudnorm nevrátil měření` / chyba ffmpegu | `brew install ffmpeg` (včetně ffprobe). |
| `Stav účtu se nepodařilo zjistit …` | Nenaběhl přibalený Codex: stupeň 1, krok 5 znovu. |
| `Překlad chybí u vět […]` | Neúplná odpověď; spustit znovu, hotové kroky se přeskočí. |
| `Už běží jiný dabing — počkej, nebo ho zruš.` | Lab dělá jednu zakázku najednou; počkat nebo Zrušit. |
| `Could not resolve to a Repository` / `404` při `gh repo clone` | Nepřijatá pozvánka, špatný `<VLASTNIK>` nebo jiný účet v `gh` (`gh auth status`). |
| Přerušená instalace nebo stahování, `No space left on device` | Zopakuj stejný příkaz (naváže); při plném disku uvolni místo (`df -h .`). Nic nemaž naslepo. |
| Instalace padá na `transformers @ git+…` | Chybí git (`xcode-select --install`) nebo internet. |
| `LocalEntryNotFoundError`, „Cannot find … outgoing traffic has been disabled", `FileNotFoundError …/config.json` (Parakeet) | Model chybí v HF cache. `./spustit.sh --kontrola` řekne který; stáhni se souhlasem (část 4). Stejné `HF_HOME` pro stažení i běh. |
| Nemotron: neznámá architektura | Starý `transformers`. Zopakuj krok 5 z části 3 (projekt připíná commit z gitu). |
| `fbaipublicfiles` / `URLError` u Demucs | V `dabing/config.py` musí zůstat `MODEL_SEPARACE = "hf://htdemucs"`. |
| `BandIt chybí` / `Silero VAD chybí` | Nefatální; stáhni `bandit` / `silero` se souhlasem. |
| BandIt SHA-256 nesedí | Smaž soubor a stáhni znovu. |
| `yt-dlp … HTTP Error 403` | `brew upgrade yt-dlp`, ověř `deno --version`. |
| `Sign in to confirm you're not a bot` | YouTube blokuje IP, obvykle kvůli VPN: přepnout VPN na jinou zemi, nebo ji vypnout. |
| `yt-dlp nenačetl video` (soukromé, věkově omezené, bot check) | Lab cookies neumí; je-li k tomu právo, stáhnout jinak a dát cestu k souboru do pole „Odkaz". |
| „Překlad Codexem potřebuje přihlášení" | Přihlášení v UI (část 5), nebo lokální překladač. |
| „Codex je přihlášený API klíčem" | V UI se odhlásit a přihlásit účtem ChatGPT. |
| `Codex překlad selhal (failed)` | Vyčerpaná kvóta, nebo model účet nemá: počkat, lokální překladač, nebo ověřit modely (část 5). |
| `Bionic / LM Studio neběží` / prázdná odpověď | „Zapnout server" v aplikaci, načíst jen jeden jazykový model, vypnout v něm přemýšlení. |
| `Worker skončil (nejspíš chyba MPS)` | Ojedinělé je v pořádku (lab restartuje). Opakované: zavřít těžké aplikace, restart labu. |
| `Worker neodpověděl do 60 / 300 / 600 s` | První start nového venv (60 s), nebo málo RAM či uspání: zavřít aplikace, Mac v zásuvce. |
| `BandIt selhal` | Lab zkusí CPU sám; ručně `DABING_DIALOG_CPU=1 ./spustit.sh`. |
| `kotva hlasu mu není dost podobná (0.00 …)` | Může skrývat chybu ECAPA: ověř model `speechbrain/spkrec-ecapa-voxceleb` a `logy/omnivoice.log`; po přesunu složky smaž `worker/omnivoice/ecapa/`. |
| Port 7872 obsazený | `lsof -nP -iTCP:7872 -sTCP:LISTEN`; spouštěč sám otevře už běžící lab. |

Postup při jakékoli chybě: 1) přečti poslední řádky logu, 2) najdi příčinu v tabulce nebo v
`docs/INSTALACE.md` kapitola 6, 3) řekni uživateli, co se stalo a co navrhuješ, 4) oprav
a ověř. Nic nemaž a nepřeinstalovávej „pro jistotu". Pokud to nevyřešíš na druhý pokus,
shrň uživateli, co jsi zkoušel, a požádej o pomoc vlastníka repozitáře (přilož poslední řádky
logu bez jakýchkoli tokenů).

## 10. Licence a právo (řekni uživateli jednou, při předání)

- **Jen nekomerční použití:** váhy OmniVoice jsou CC-BY-NC (a BandIt Plus je odvozený od
  CC-BY-NC-4.0). Monetizace dabovaného videa je komerční použití a licence ho nepovolují.
- **Souhlas mluvčích:** lab naklonuje hlas každého mluvčího v dabovaném videu. Souhlas je potřeba
  od všech mluvčích (u vlastní nahrávky od uživatele a lidí, kteří v ní mluví). U cizího videa bez
  jejich souhlasu dabing nezveřejňovat.
- **Respektuj autorská práva** dabovaného videa a podmínky YouTube; zveřejnit dabing cizího videa
  bez svolení nedoporučujeme.
- **Označ dabing jako vytvořený AI** (YouTube požaduje přiznání realistických syntetických hlasů;
  mohou platit i evropská pravidla, AI Act čl. 50, ověř aktuální znění).
- Uvedení autorů: Parakeet TDT v3 (CC-BY-4.0, NVIDIA), OmniVoice (CC-BY-NC, k2-fsa) a BandIt Plus
  (CC-BY-NC-4.0) vyžadují uvedení původu. Podsložka `audio_tokenizer/` modelu OmniVoice je pod
  Boson Higgs Audio 2 Community License, která při šíření modelu nebo produktu s ním vyžaduje uvedení
  „Built with Higgs Materials licensed from Boson AI USA, Inc." (plné znění je v LICENSE na kartě
  modelu). Při sdílení výsledků nebo modelů přečti podmínky na kartě každého modelu.
- Toto jsou technická upozornění, ne právní rada.

## 11. Předání uživateli

Na konci shrň: co je nainstalováno (verze, `./spustit.sh --kontrola` prošlo), jak lab spustit
(`cd` do složky a `./spustit.sh`, adresa `http://127.0.0.1:7872`), že je přihlášený ke Codexu
(nebo že jede lokálně), výsledek zkoušky, kolik místa zabírá a kde je `jobs/`, co čekat
z hlediska času a kvóty, a legální upozornění z kapitoly 10. Připomeň, že `codex_home/auth.json`
je jeho přihlášení a nikomu se neposílá. Nabídni úklid zkušební zakázky.
