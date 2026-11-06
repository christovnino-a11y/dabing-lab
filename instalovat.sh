#!/usr/bin/env bash
# Dabing lab - instalace jedním příkazem (a zároveň aktualizace; bezpečně jde spouštět opakovaně).
#
#   ./instalovat.sh                  nainstaluje vše potřebné, zeptá se na stažení modelů a spustí lab
#   ./instalovat.sh --zkouska        nic nemění: jen zkontroluje stav a vypíše, co by se stalo
#   ./instalovat.sh --ano            bez otázek (včetně stažení modelů, souhlas dáváš tímto přepínačem)
#   ./instalovat.sh --bez-modelu     modely nestahovat (a nic se nezeptat)
#   ./instalovat.sh --bez-spusteni   na konci lab nespouštět
#   ./instalovat.sh -h               tato nápověda
#
# Z internetu:
#   curl -fsSL https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/instalovat.sh | bash
# Mimo klon repozitáře se naklonuje do ~/dabing-lab (jinou složku zvolí proměnná DABING_SLOZKA)
# a pokračuje se v ní. Otázky se čtou z terminálu (/dev/tty); bez terminálu a bez --ano se nic nestahuje.
#
# Kroky: 1 Mac, 2 Command Line Tools, 3 Homebrew, 4 nástroje, 5 aktualizace gitu,
#        6 Python prostředí, 7 modely, 8 příkaz dabing, kontrola a spuštění.
# Ollama ani LM Studio se neinstalují: překladač se volí v aplikaci.
#
# Kompatibilní s bash 3.2 (systémový bash na macOS). Podrobný ruční postup: docs/INSTALACE.md

set -euo pipefail

REPO_URL="https://github.com/christovnino-a11y/dabing-lab.git"
CIL_VYCHOZI="${DABING_SLOZKA:-$HOME/dabing-lab}"
CELKEM=8
MIN_GB=15          # první instalace; aktualizace hotové instalace potřebuje jen MIN_GB_AKTUALIZACE
MIN_GB_AKTUALIZACE=2
KLICE_MODELU="parakeet nemotron htdemucs omnivoice ecapa bandit silero"

# PATH před úpravou (kvůli kontrole, jestli je ~/.local/bin v PATH uživatele).
PATH_PUVODNI="$PATH"
# Z čistého terminálu bývá PATH bez Homebrew a uv (stejně jako ve spustit.sh).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

ZKOUSKA=0
ANO=0
BEZ_MODELU=0
BEZ_SPUSTENI=0
MA_TTY=0
PROJEKT=""
ORIG_ARGS=""
HLASENO=0
AKT_KROK=0
MODELY_PRESKOCENY=0
BREW_CHYBI=0

pouziti() {
    cat <<'KONEC'
Dabing lab - instalace jedním příkazem (a aktualizace).

  ./instalovat.sh                  nainstaluje vše potřebné, zeptá se na stažení modelů a spustí lab
  ./instalovat.sh --zkouska        nic nemění: jen zkontroluje stav a vypíše, co by se stalo
  ./instalovat.sh --ano            bez otázek (včetně stažení modelů, souhlas dáváš tímto přepínačem)
  ./instalovat.sh --bez-modelu     modely nestahovat (a nic se nezeptat)
  ./instalovat.sh --bez-spusteni   na konci lab nespouštět
  ./instalovat.sh -h               tato nápověda

Z internetu:
  curl -fsSL https://raw.githubusercontent.com/christovnino-a11y/dabing-lab/main/instalovat.sh | bash
Mimo klon repozitáře se naklonuje do ~/dabing-lab (jinou složku zvolí proměnná DABING_SLOZKA).
KONEC
}

# --- Výpisy
krok() { AKT_KROK="$1"; echo; echo "[$1/$CELKEM] $2"; }
info() { echo "      $*"; }
zk() { echo "      Zkouška: $*"; }
preskocen() { [[ "${DABING_PRESKOC:-0}" -ge "$1" ]]; }

selhal() {  # $1 = číslo kroku, $2 = co selhalo, $3 = co dělat
    HLASENO=1
    {
        echo
        echo "CHYBA v kroku [$1/$CELKEM]: $2"
        [[ -z "${3:-}" ]] || echo "      Co dělat: $3"
        echo "      Instalace jde bezpečně spustit znovu, hotové kroky se přeskočí."
    } >&2
    exit 1
}

zaverecna_zprava() {
    local kod=$?
    if [[ $kod -ne 0 && $HLASENO -eq 0 && $kod -ne 2 ]]; then
        echo >&2
        echo "CHYBA: instalace se v kroku [$AKT_KROK/$CELKEM] nečekaně zastavila (kód $kod). Chybu hledej o pár řádků výš." >&2
        echo "      Instalace jde bezpečně spustit znovu. Pomoc: docs/INSTALACE.md, kapitola Řešení potíží." >&2
    fi
}
trap zaverecna_zprava EXIT

# Otázka [A/n] z terminálu (stdin je při curl | bash roura). Vrací 0 = ano.
ptej() {
    local odp=""
    if [[ $ANO -eq 1 ]]; then
        info "$1 -> ano (přepínač --ano)"
        return 0
    fi
    if [[ $MA_TTY -eq 0 ]]; then
        info "$1 -> bez terminálu výchozí odpověď: ne"
        return 1
    fi
    printf '      %s [A/n] ' "$1" >/dev/tty
    read -r odp </dev/tty || odp="n"
    case "$odp" in
    "" | a | A | ano | Ano | ANO | y | Y | yes) return 0 ;;
    *) return 1 ;;
    esac
}

zabran_bez_terminalu() {
    [[ $ZKOUSKA -eq 1 || $ANO -eq 1 || $MA_TTY -eq 1 ]] && return 0
    {
        echo
        echo "Není tu terminál, takže se není koho zeptat, a bez --ano se nic neinstaluje ani nestahuje."
        echo "Spusť instalaci ve vlastním okně Terminálu, nebo po souhlasu uživatele s přepínačem --ano"
        echo "(nejdřív zkus --zkouska: vypíše, co by se stalo)."
    } >&2
    exit 2
}

je_checkout() {
    [[ -f "$1/spustit.sh" && -f "$1/scripts/stahni_modely.sh" && -d "$1/dabing" ]]
}

najdi_projekt() {
    local zdroj adresar
    zdroj="${BASH_SOURCE[0]:-}"
    if [[ -n "$zdroj" && -f "$zdroj" ]]; then
        while [[ -L "$zdroj" ]]; do
            adresar="$(cd -P "$(dirname "$zdroj")" && pwd)"
            zdroj="$(readlink "$zdroj")"
            [[ "$zdroj" != /* ]] && zdroj="$adresar/$zdroj"
        done
        adresar="$(cd -P "$(dirname "$zdroj")" && pwd)"
        if je_checkout "$adresar"; then
            PROJEKT="$adresar"
            return 0
        fi
    fi
    # Spuštěno z roury (curl | bash) uvnitř klonu? Bez volání gitu (na čistém Macu by otevřel okno CLT).
    if je_checkout "$PWD"; then
        PROJEKT="$(pwd -P)"
    fi
    return 0
}

existujici_rodic() {  # nejbližší existující složka nad cestou (kvůli df)
    local c="$1"
    while [[ ! -d "$c" && "$c" != "/" && "$c" != "." ]]; do
        c="$(dirname "$c")"
    done
    echo "$c"
}

# --- [1/8] Mac
krok_mac() {
    preskocen 1 && return 0
    krok 1 "Kontrola Macu (Apple Silicon, macOS 14 nebo novější, volné místo)"
    [[ "$(uname -s)" == "Darwin" ]] || selhal 1 "Tohle není macOS." "Dabing lab běží jen na Macu s čipem Apple Silicon."
    if [[ "$(uname -m)" != "arm64" ]]; then
        if [[ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
            selhal 1 "Terminál běží pod Rosettou (uname -m: $(uname -m))." "Otevři Terminál bez Rosetty (Získat informace, odškrtnout Otevřít pomocí Rosetty) a spusť instalaci znovu."
        fi
        selhal 1 "Mac nemá čip Apple Silicon (uname -m: $(uname -m))." "Dabing lab běží jen na Macu s čipem M1 a novějším."
    fi
    local macos
    macos="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
    [[ "$macos" =~ ^[0-9]+$ ]] || macos=0
    [[ "$macos" -ge 14 ]] || selhal 1 "Potřebuji macOS 14 (Sonoma) nebo novější, tady je $(sw_vers -productVersion 2>/dev/null || echo '?')." "Aktualizuj macOS v Nastavení systému."
    local kde volne_kb volne_gb
    kde="$(existujici_rodic "${PROJEKT:-$CIL_VYCHOZI}")"
    volne_kb="$(df -Pk "$kde" 2>/dev/null | awk 'NR==2 {print $4}')"
    [[ "$volne_kb" =~ ^[0-9]+$ ]] || volne_kb=0
    volne_gb=$((volne_kb / 1024 / 1024))
    local minimum=$MIN_GB
    [[ -n "${PROJEKT:-}" && -x "$PROJEKT/.venv/bin/python" ]] && minimum=$MIN_GB_AKTUALIZACE
    if [[ $volne_gb -lt $minimum ]]; then
        selhal 1 "Volné místo je jen $volne_gb GB, potřebuji aspoň $minimum GB." "Uvolni místo na disku (df -h) a spusť instalaci znovu."
    fi
    info "OK: arm64, macOS $macos, volno $volne_gb GB."
}

# --- [2/8] Command Line Tools
clt_je() {
    local c
    c="$(xcode-select -p 2>/dev/null)" || return 1
    [[ -x "$c/usr/bin/git" ]]
}

krok_clt() {
    preskocen 2 && return 0
    krok 2 "Command Line Tools (obsahují git)"
    if clt_je; then
        info "Už nainstalovány."
        return 0
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        zk "spustil bych xcode-select --install a počkal, až instalace v otevřeném okně doběhne."
        return 0
    fi
    zabran_bez_terminalu
    info "Spouštím xcode-select --install: v okně, které se otevře, potvrď instalaci (trvá pár minut)."
    xcode-select --install >/dev/null 2>&1 || true
    local n=0
    until clt_je; do
        n=$((n + 1))
        [[ $n -le 720 ]] || selhal 2 "Command Line Tools se do hodiny nenainstalovaly." "Dokonči instalaci v okně macOS (nebo spusť xcode-select --install) a spusť skript znovu."
        sleep 5
    done
    info "Hotovo."
}

# --- [3/8] Homebrew
najdi_brew() {
    local b
    for b in "$(command -v brew 2>/dev/null || true)" /opt/homebrew/bin/brew; do
        if [[ -n "$b" && -x "$b" ]]; then
            eval "$("$b" shellenv)"
            return 0
        fi
    done
    return 1
}

krok_brew() {
    if preskocen 3; then
        najdi_brew || true
        return 0
    fi
    krok 3 "Homebrew"
    if najdi_brew; then
        info "OK: $(brew --version 2>/dev/null | head -n 1)"
        return 0
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        BREW_CHYBI=1
        zk "Homebrew chybí; zeptal bych se a pustil oficiální instalátor (heslo k Macu zadáš sám)."
        return 0
    fi
    ptej "Homebrew chybí. Nainstalovat ho oficiálním instalátorem (zeptá se na heslo k Macu)?" \
        || selhal 3 "Bez Homebrew nejde pokračovat." "Nainstaluj ho ručně (docs/INSTALACE.md, krok 2) a spusť skript znovu."
    local instalator
    instalator="$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || selhal 3 "Stažení instalátoru Homebrew selhalo." "Zkontroluj internet a spusť skript znovu."
    if [[ $MA_TTY -eq 1 ]]; then
        /bin/bash -c "$instalator" </dev/tty || selhal 3 "Instalátor Homebrew skončil chybou." "Přečti výpis výše, případně postupuj ručně podle docs/INSTALACE.md, krok 2."
    else
        /bin/bash -c "$instalator" || selhal 3 "Instalátor Homebrew skončil chybou." "Spusť skript ve vlastním okně Terminálu (instalátor se ptá na heslo)."
    fi
    najdi_brew || selhal 3 "Homebrew se nainstaloval, ale nenašel jsem příkaz brew." "Otevři nové okno Terminálu a spusť skript znovu."
    if ! grep -qs 'brew shellenv' "$HOME/.zprofile"; then
        info "Přidávám do ~/.zprofile řádek, aby Terminál příště našel příkaz brew."
        printf '\n# Homebrew (přidal Dabing lab)\neval "$(%s shellenv)"\n' "$(command -v brew)" >>"$HOME/.zprofile"
    fi
    info "Hotovo: $(brew --version 2>/dev/null | head -n 1)"
}

# --- [4/8] Nástroje
krok_nastroje() {
    preskocen 4 && return 0
    krok 4 "Nástroje (ffmpeg, yt-dlp, deno, uv)"
    local chybi="" n
    for n in ffmpeg yt-dlp deno uv; do
        command -v "$n" >/dev/null 2>&1 || chybi="$chybi $n"
    done
    if ! command -v ffprobe >/dev/null 2>&1; then
        case " $chybi " in *" ffmpeg "*) ;; *) chybi="$chybi ffmpeg" ;; esac
    fi
    chybi="${chybi# }"
    if [[ -z "$chybi" ]]; then
        info "OK: vše je k dispozici (uv: $(command -v uv))."
        return 0
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        zk "doinstaloval bych přes Homebrew jen chybějící: brew install $chybi"
        return 0
    fi
    info "Chybí: $chybi. Instaluji přes Homebrew."
    # shellcheck disable=SC2086
    brew install $chybi || selhal 4 "brew install $chybi selhal." "Přečti chybu výše, spusť brew doctor, a skript spusť znovu."
    for n in $chybi; do
        [[ "$n" == "ffmpeg" ]] && n="ffprobe"
        command -v "$n" >/dev/null 2>&1 || selhal 4 "Po instalaci pořád chybí $n." "Otevři nové okno Terminálu a spusť skript znovu."
    done
    info "Hotovo."
}

# --- [5/8] Aktualizace z gitu (nikdy neshodí instalaci)
git_aktualizuj() {  # $1 = složka; vrací 0 = pull proběhl (změnu zjistíš přes GIT_ZMENA), 1 = přeskočeno
    local d="$1" vetev
    GIT_ZMENA=0
    git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || { info "Není to git klon, aktualizaci přeskakuji."; return 1; }
    vetev="$(git -C "$d" symbolic-ref --short -q HEAD 2>/dev/null || true)"
    [[ "$vetev" == "main" ]] || { info "Větev je '${vetev:-?}', ne main: aktualizaci přeskakuji."; return 1; }
    [[ -z "$(git -C "$d" status --porcelain 2>/dev/null)" ]] \
        || { info "Pracovní složka má neuložené změny: aktualizaci přeskakuji (nic nepřepisuji)."; return 1; }
    git -C "$d" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 \
        || { info "Větev nemá nastavený upstream: aktualizaci přeskakuji."; return 1; }
    if [[ $ZKOUSKA -eq 1 ]]; then
        zk "provedl bych git pull --ff-only (čistá složka, větev main)."
        return 0
    fi
    local pred po s_pred s_po
    pred="$(git -C "$d" rev-parse HEAD)"
    s_pred="$(git -C "$d" rev-parse HEAD:instalovat.sh 2>/dev/null || true)"
    if GIT_TERMINAL_PROMPT=0 git -C "$d" pull --ff-only --quiet 2>/dev/null; then
        po="$(git -C "$d" rev-parse HEAD)"
        s_po="$(git -C "$d" rev-parse HEAD:instalovat.sh 2>/dev/null || true)"
        if [[ "$pred" == "$po" ]]; then
            info "Kód je aktuální."
        else
            info "Kód aktualizován."
            [[ "$s_pred" == "$s_po" ]] || GIT_ZMENA=2  # změnil se i tento skript
        fi
        return 0
    fi
    info "git pull se nepovedl (offline, nebo se větve rozešly). Pokračuji s tím, co je na disku."
    return 1
}

krok_git() {
    preskocen 5 && return 0
    krok 5 "Aktualizace kódu (git pull --ff-only)"
    if [[ -z "$PROJEKT" ]]; then
        zk "složka projektu ještě neexistuje (nejdřív by se naklonovala), pull není potřeba."
        return 0
    fi
    if [[ -n "${DABING_PO_KLONU:-}" ]]; then
        info "Právě naklonováno, aktualizace není třeba."
        return 0
    fi
    git_aktualizuj "$PROJEKT" || true
    if [[ "${GIT_ZMENA:-0}" -eq 2 && $ZKOUSKA -eq 0 && -f "$PROJEKT/instalovat.sh" ]]; then
        info "Instalační skript se aktualizoval, spouštím ho znovu v nové verzi."
        # shellcheck disable=SC2086
        exec env DABING_PRESKOC=5 bash "$PROJEKT/instalovat.sh" $ORIG_ARGS
    fi
    return 0
}

# --- [6/8] Python prostředí (přesně podle docs/INSTALACE.md, krok 5)
krok_python() {
    preskocen 6 && return 0
    krok 6 "Python prostředí (.venv a worker/omnivoice/.venv)"
    if [[ -z "$PROJEKT" ]]; then
        zk "v klonu bych spustil: uv sync --frozen (nebo uv venv + uv pip install -r pyproject.toml, když chybí uv.lock) a (cd worker/omnivoice && uv venv + uv pip install -r pyproject.toml)."
        return 0
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        [[ -x "$PROJEKT/.venv/bin/python" ]] && info ".venv už existuje (uv jen zkontroluje a doplní)." || info ".venv chybí (vytvořil bych ho)."
        [[ -x "$PROJEKT/worker/omnivoice/.venv/bin/python" ]] && info "worker/omnivoice/.venv už existuje." || info "worker/omnivoice/.venv chybí (vytvořil bych ho)."
        if [[ -f "$PROJEKT/uv.lock" ]]; then
            zk "(cd $PROJEKT && uv sync --frozen) a (cd worker/omnivoice && uv venv + uv pip install -r pyproject.toml)"
        else
            zk "(cd $PROJEKT && uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml) a (cd worker/omnivoice && uv venv + uv pip install -r pyproject.toml)"
        fi
        command -v uv >/dev/null 2>&1 || zk "uv zatím chybí (doinstaluje se v kroku 4)."
        return 0
    fi
    command -v uv >/dev/null 2>&1 || selhal 6 "Nenašel jsem příkaz uv." "Nainstaluj ho (brew install uv) a spusť skript znovu."
    info "Hlavní prostředí: stahuje torch a další balíčky, na čistém Macu to trvá několik minut."
    cd "$PROJEKT"
    if [[ -f uv.lock ]]; then
        uv sync --frozen || selhal 6 "uv sync --frozen selhal." "Zkontroluj internet a git (Command Line Tools) a skript spusť znovu; potíže: docs/INSTALACE.md, Řešení potíží."
    else
        if ! { uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml; }; then
            selhal 6 "Instalace hlavního prostředí selhala." "Zkontroluj internet a git, případně uv python install 3.12, a skript spusť znovu."
        fi
    fi
    info "Prostředí hlasu (worker/omnivoice)."
    # bez uv sync: ten zapíše uv.lock (složka by přestala být čistá a aktualizace by se přeskakovaly)
    # a při opakovaném běhu by znovu řešil volné verze ve fungujícím prostředí hlasu
    (cd worker/omnivoice && uv venv --python 3.12 --allow-existing && uv pip install -r pyproject.toml) \
        || selhal 6 "Instalace prostředí hlasu (worker/omnivoice) selhala." "Zkontroluj internet a skript spusť znovu."
    [[ -x ".venv/bin/python" && -x "worker/omnivoice/.venv/bin/python" ]] \
        || selhal 6 "Po instalaci chybí .venv nebo worker/omnivoice/.venv." "Spusť skript znovu a přečti výpis uv."
    info "Hotovo."
}

# --- [7/8] Modely
krok_modely() {
    preskocen 7 && return 0
    krok 7 "Modely (zhruba 6,5 GB, jednou)"
    if [[ -z "$PROJEKT" ]]; then
        zk "ukázal bych shrnutí modelů (./scripts/stahni_modely.sh --list), zeptal se jednou [A/n] a po souhlasu stáhl: $KLICE_MODELU"
        return 0
    fi
    local vypis chybi_klice seznam
    vypis="$("$PROJEKT/scripts/stahni_modely.sh" --list 2>/dev/null)" || vypis=""
    chybi_klice="$(printf '%s\n' "$vypis" | awk '/^\[[a-z]+\]/ {k=$1; gsub(/[\[\]]/, "", k)} /stav: +chyb/ {printf "%s ", k}')"
    if [[ -n "$vypis" && -z "$chybi_klice" ]]; then
        info "OK: všechny modely jsou už stažené."
        return 0
    fi
    seznam="$(printf '%s\n' "$vypis" | awk '/^\[[a-z]+\]/ {n=$0; sub(/^\[[a-z]+\] /, "", n)} /velikost:/ {v=$0; sub(/^ *velikost: */, "", v); sub(/ \(.*/, "", v)} /stav: +chyb/ {print "      - " n " (" v ")"}')"
    if [[ -n "$seznam" ]]; then
        info "Chybí stáhnout (celá sada má zhruba 6,5 GB):"
        echo "$seznam"
    else
        info "Stav modelů nelze zjistit, celá sada má zhruba 6,5 GB: $KLICE_MODELU"
    fi
    info "Licence: váhy OmniVoice jsou CC-BY-NC = JEN NEKOMERČNĚ (dabing nesmíš zpeněžit)."
    info "         BandIt Plus je odvozený od modelu CC-BY-NC-4.0, také nekomerčně."
    info "         Ostatní: Parakeet CC-BY-4.0, Nemotron OpenMDW, Silero MIT; vše: ./scripts/stahni_modely.sh --list"
    info "         Klonuje se hlas každého mluvčího: potřebuješ jejich souhlas."
    if [[ $BEZ_MODELU -eq 1 ]]; then
        MODELY_PRESKOCENY=1
        info "Přeskakuji (--bez-modelu). Později: ./scripts/stahni_modely.sh"
        return 0
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        zk "zeptal bych se jednou [A/n]; po souhlasu ./scripts/stahni_modely.sh --ano $KLICE_MODELU"
        return 0
    fi
    if ptej "Stáhnout modely teď?"; then
        # shellcheck disable=SC2086
        "$PROJEKT/scripts/stahni_modely.sh" --ano $KLICE_MODELU \
            || selhal 7 "Stažení modelů selhalo." "Zkontroluj internet a volné místo; stáhni znovu: ./scripts/stahni_modely.sh"
        info "Hotovo."
    else
        MODELY_PRESKOCENY=1
        info "Přeskočeno. Stáhneš je později (ptá se po jednom): ./scripts/stahni_modely.sh"
        info "Nebo všechny najednou: ./scripts/stahni_modely.sh --ano $KLICE_MODELU"
    fi
}

# --- [8/8] Příkaz dabing, kontrola a spuštění
krok_dabing() {
    krok 8 "Příkaz dabing, kontrola a spuštění"
    local odkaz="$HOME/.local/bin/dabing" cil existuje pridat=0
    if [[ -z "$PROJEKT" ]]; then
        zk "vytvořil bych odkaz ~/.local/bin/dabing na spustit.sh (pokud jiný příkaz dabing neexistuje), případně přidal ~/.local/bin do PATH v ~/.zshrc (po upozornění), spustil ./spustit.sh --kontrola a lab."
        return 0
    fi
    cil="$PROJEKT/spustit.sh"
    existuje="$(command -v dabing 2>/dev/null || true)"
    if [[ -n "$existuje" ]]; then
        if [[ "$(readlink "$existuje" 2>/dev/null || true)" == "$cil" ]]; then
            info "Příkaz dabing už míří na tento klon ($existuje)."
        else
            info "Příkaz dabing už existuje jinde ($existuje), ponechávám ho beze změny."
            info "Tenhle klon spustíš: $cil"
        fi
    else
        if [[ $ZKOUSKA -eq 1 ]]; then
            zk "vytvořil bych odkaz $odkaz -> $cil"
        else
            mkdir -p "$HOME/.local/bin"
            if [[ -L "$odkaz" && ! -e "$odkaz" ]]; then
                info "Opravuji nefunkční odkaz $odkaz."
                rm -f "$odkaz"
            fi
            if [[ -e "$odkaz" || -L "$odkaz" ]]; then
                info "$odkaz už existuje a není to funkční příkaz dabing, nechávám ho být. Spuštění: $cil"
            else
                ln -s "$cil" "$odkaz"
                info "Vytvořen odkaz $odkaz -> $cil"
            fi
        fi
        # ~/.local/bin v PATH uživatele (před mou úpravou PATH)?
        case ":$PATH_PUVODNI:" in
        *":$HOME/.local/bin:"*) ;;
        *) pridat=1 ;;
        esac
        if [[ $pridat -eq 1 ]]; then
            if grep -qs '\.local/bin' "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.zshenv"; then
                info "~/.local/bin už je v nastavení zsh, nový Terminál ho najde."
            elif [[ $ZKOUSKA -eq 1 ]]; then
                zk "oznámil bych a přidal do ~/.zshrc řádek: export PATH=\"\$HOME/.local/bin:\$PATH\""
            else
                info "Přidávám do ~/.zshrc řádek, aby Terminál našel příkaz dabing:"
                info "    export PATH=\"\$HOME/.local/bin:\$PATH\""
                printf '\n# Dabing lab: příkaz dabing\nexport PATH="$HOME/.local/bin:$PATH"\n' >>"$HOME/.zshrc"
                info "Platí až v novém okně Terminálu."
            fi
        fi
    fi
    if [[ $ZKOUSKA -eq 1 ]]; then
        zk "spustil bych ./spustit.sh --kontrola (čte jen stav prostředí a modelů)."
        if [[ $BEZ_SPUSTENI -eq 1 ]]; then
            zk "lab bych nespouštěl (--bez-spusteni)."
        else
            zk "při úspěšné kontrole bych spustil ./spustit.sh (otevře prohlížeč), bez modelů ne."
        fi
        return 0
    fi
    info "Kontrola prostředí (./spustit.sh --kontrola):"
    if ! "$cil" --kontrola; then
        if [[ $MODELY_PRESKOCENY -eq 1 ]]; then
            echo
            echo "Instalace je hotová, ale kontrola našla chybějící modely, takže lab zatím nespouštím."
            echo "Stáhni je a spusť lab:"
            echo "    ./scripts/stahni_modely.sh      (ptá se po jednom; nebo --ano s názvy modelů)"
            echo "    dabing                          (nebo $cil)"
            exit 0
        fi
        selhal 8 "Kontrola prostředí našla chyby (výpis výše)." "Oprav je podle hlášek (docs/INSTALACE.md, Řešení potíží) a spusť skript znovu."
    fi
}

zaver() {
    echo
    echo "Překladač (Codex, Ollama, LM Studio) si vybereš v aplikaci; nic z toho tento skript neinstaluje."
    if [[ $BEZ_SPUSTENI -eq 1 ]]; then
        echo "Hotovo. Lab spustíš příkazem dabing (v novém okně Terminálu), nebo: $PROJEKT/spustit.sh"
        return 0
    fi
    echo "Hotovo. Spouštím lab; příště stačí příkaz dabing. Ukončení: Ctrl+C."
    exec "$PROJEKT/spustit.sh"
}

main() {
    ORIG_ARGS="$*"
    local a
    for a in "$@"; do
        case "$a" in
        --zkouska) ZKOUSKA=1 ;;
        --ano) ANO=1 ;;
        --bez-modelu) BEZ_MODELU=1 ;;
        --bez-spusteni) BEZ_SPUSTENI=1 ;;
        -h | --help) pouziti; exit 0 ;;
        *) echo "Neznámý přepínač: $a (viz -h)" >&2; exit 2 ;;
        esac
    done
    # Otázky čteme z /dev/tty, protože stdin může být roura (curl | bash).
    if ( : </dev/tty ) 2>/dev/null; then MA_TTY=1; fi

    echo "Dabing lab: instalace"
    [[ $ZKOUSKA -eq 0 ]] || echo "ZKOUŠKA: nic se nemění, nestahuje a na nic se neptám; jen ověřím stav a vypíšu, co by se stalo."

    najdi_projekt
    if [[ -z "$PROJEKT" ]]; then
        # Mimo klon (typicky curl | bash): Mac a git, pak naklonovat a pokračovat v klonu.
        krok_mac
        if je_checkout "$CIL_VYCHOZI"; then
            PROJEKT="$(cd -P "$CIL_VYCHOZI" && pwd)"
        fi
        zabran_bez_terminalu
        krok_clt
        if [[ -z "$PROJEKT" ]]; then
            if [[ $ZKOUSKA -eq 1 ]]; then
                echo
                zk "naklonoval bych $REPO_URL do $CIL_VYCHOZI a pokračoval tam (samotná zkouška nic neklonuje)."
            else
                if [[ -e "$CIL_VYCHOZI" ]] && [[ -n "$(ls -A "$CIL_VYCHOZI" 2>/dev/null)" ]]; then
                    selhal 2 "Složka $CIL_VYCHOZI existuje, ale není to klon Dabing labu." "Přesuň ji, nebo zvol jinou: DABING_SLOZKA=/cesta curl ... | bash"
                fi
                echo
                echo "Klonuji $REPO_URL do $CIL_VYCHOZI"
                if ! GIT_TERMINAL_PROMPT=0 git clone --quiet "$REPO_URL" "$CIL_VYCHOZI"; then
                    HLASENO=1
                    {
                        echo
                        echo "CHYBA: repozitář se nepodařilo naklonovat. Zkontroluj připojení k internetu"
                        echo "      a spusť příkaz znovu. Ručně to jde takto:"
                        echo "          git clone $REPO_URL ~/dabing-lab && ~/dabing-lab/instalovat.sh"
                    } >&2
                    exit 1
                fi
                PROJEKT="$(cd -P "$CIL_VYCHOZI" && pwd)"
                export DABING_PO_KLONU=1
            fi
        else
            info "Našel jsem klon: $PROJEKT"
            if [[ $ZKOUSKA -eq 0 ]]; then
                git_aktualizuj "$PROJEKT" || true
            fi
        fi
        if [[ $ZKOUSKA -eq 0 && -f "$PROJEKT/instalovat.sh" ]]; then
            # Dál platí instalátor z klonu (kroky 1 a 2 jsou hotové).
            # shellcheck disable=SC2086
            exec env DABING_PRESKOC=2 bash "$PROJEKT/instalovat.sh" $ORIG_ARGS
        fi
        DABING_PRESKOC=2
    fi

    krok_mac
    zabran_bez_terminalu
    krok_clt
    krok_brew
    krok_nastroje
    krok_git
    krok_python
    krok_modely
    krok_dabing
    if [[ $ZKOUSKA -eq 1 ]]; then
        echo
        echo "Konec zkoušky: nic se nezměnilo. Ostrá instalace: ./instalovat.sh"
        return 0
    fi
    zaver
}

main "$@"; exit $?
