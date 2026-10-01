#!/usr/bin/env bash
# Dabing lab - přenosný spouštěč (jen relativní cesty, žádné údaje o konkrétním počítači).
#
#   ./spustit.sh             spustí web na http://127.0.0.1:7872 a otevře prohlížeč
#   ./spustit.sh --kontrola  jen ověří prostředí, nástroje a modely; nic nespustí
#
# Volitelné proměnné prostředí:
#   LAB_NO_OPEN=1         neotevírat prohlížeč
#   LAB_NO_CAFFEINATE=1   nebránit uspání Macu (výchozí: během běhu se Mac neuspí)
#   DABING_DIALOG_CPU=1   BandIt (jásot, smích) počítat na procesoru místo grafiky
#
# Ukončení: Ctrl+C. Kompatibilní s bash 3.2 (systémový bash na macOS).
# Instalace od nuly: docs/INSTALACE.md

set -euo pipefail

# Kam odkázat uživatele, když chybí modely.
NAPOVEDA_MODELY="stáhni je: ./scripts/stahni_modely.sh, viz docs/INSTALACE.md, krok Modely"

# --- Kde je projekt (i když je spouštěč odkaz, např. ~/.local/bin/dabing)
zdroj="${BASH_SOURCE[0]}"
while [[ -L "$zdroj" ]]; do
    adresar="$(cd -P "$(dirname "$zdroj")" && pwd)"
    zdroj="$(readlink "$zdroj")"
    [[ "$zdroj" != /* ]] && zdroj="$adresar/$zdroj"
done
PROJEKT="$(cd -P "$(dirname "$zdroj")" && pwd)"
cd "$PROJEKT"

# Z Finderu nebo z vlastního terminálu bývá PATH bez Homebrew a uv; yt-dlp pak nenajde ani deno.
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$PATH"

PY=".venv/bin/python"
WORKER_PY="worker/omnivoice/.venv/bin/python"
PORT="$("$PY" -c 'from dabing.config import PORT; print(PORT)' 2>/dev/null || echo 7872)"
[[ "$PORT" =~ ^[0-9]+$ ]] || PORT=7872
URL="http://127.0.0.1:$PORT"

chyby=0
varovani=0
chyba() { echo "CHYBA: $*" >&2; chyby=$((chyby + 1)); }
pozor() { echo "Pozor: $*" >&2; varovani=$((varovani + 1)); }

# Existuje aspoň jeden soubor odpovídající vzoru (hvězdičky)? Funguje i s mezerami v cestě.
ma_soubor() {
    local f
    while IFS= read -r f; do
        [[ -e "$f" ]] && return 0
    done < <(compgen -G "$1" || true)
    return 1
}

kontrola() {
    # počítač
    [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] \
        || chyba "Dabing lab běží jen na Macu s čipem Apple Silicon (M1 a novější)."
    local macos
    macos="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
    [[ "${macos:-0}" -ge 14 ]] || chyba "Potřebuji macOS 14 (Sonoma) nebo novější, tady je ${macos:-?}."

    # prostředí
    [[ -x "$PY" ]] || chyba "Chybí prostředí .venv - viz docs/INSTALACE.md, krok Python prostředí."
    [[ -x "$WORKER_PY" ]] \
        || chyba "Chybí prostředí hlasu. Spusť: (cd worker/omnivoice && uv sync)"

    # nástroje
    local n
    for n in ffmpeg ffprobe yt-dlp; do
        command -v "$n" >/dev/null 2>&1 || chyba "Chybí $n. Spusť: brew install ffmpeg yt-dlp"
    done
    command -v deno >/dev/null 2>&1 \
        || pozor "Chybí deno - yt-dlp bez něj nemusí stáhnout video z YouTube (brew install deno)."

    # modely z lokální HF cache (běh je offline, nic se nestahuje)
    local cache="${HF_HUB_CACHE:-${HF_HOME:+$HF_HOME/hub}}"
    cache="${cache:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface/hub}"
    local radek repo soubor popis zbytek chybi=""
    for radek in \
        "mlx-community/parakeet-tdt-0.6b-v3|model.safetensors|přepis (Parakeet)" \
        "nvidia/Nemotron-3-Diarization|model.safetensors|mluvčí (Nemotron)" \
        "k2-fsa/OmniVoice|model.safetensors|hlas (OmniVoice)" \
        "k2-fsa/OmniVoice|audio_tokenizer/model.safetensors|hlas (OmniVoice, tokenizér)" \
        "speechbrain/spkrec-ecapa-voxceleb|embedding_model.ckpt|kontrola podobnosti hlasu (ECAPA)" \
        "adefossez/HTDemucs|955717e8.safetensors|oddělení hlasu od pozadí (Demucs)"; do
        repo="${radek%%|*}"
        zbytek="${radek#*|}"
        soubor="${zbytek%%|*}"
        popis="${zbytek#*|}"
        ma_soubor "$cache/models--${repo//\//--}/snapshots/*/$soubor" \
            || chybi="$chybi"$'\n'"    - $repo: $popis"
    done
    [[ -z "$chybi" ]] \
        || chyba "V HF cache ($cache) chybí povinné modely, bez nich příslušný krok selže:$chybi
    ($NAPOVEDA_MODELY)"
    ma_soubor "modely/bandit_plus/model_bandit_plus_dnr_sdr_11.47.chpt" \
        || pozor "Chybí BandIt (modely/bandit_plus): jásot a smích přes řeč zmizí spolu s hlasem."
    ma_soubor "$HOME/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.jit" \
        || pozor "Chybí Silero VAD: podklad se bude řídit jen časy slov, ne detekcí řeči."
}

kontrola

if [[ "${1:-}" == "--kontrola" ]]; then
    if [[ $chyby -eq 0 && $varovani -eq 0 ]]; then
        echo "Vše v pořádku."
    else
        echo "Chyb: $chyby, upozornění: $varovani."
    fi
    [[ $chyby -eq 0 ]]
    exit $?
fi
if [[ $chyby -gt 0 ]]; then
    echo "Opravuj chyby výše a spusť ./spustit.sh znovu (./spustit.sh --kontrola jen ověří stav)." >&2
    exit 1
fi

# --- Port: když už lab běží, jen otevřít prohlížeč
pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
if [[ -n "$pid" ]]; then
    if ps -o command= -p "$pid" 2>/dev/null | grep -q "app.py"; then
        echo "Dabing lab už běží: $URL"
        [[ "${LAB_NO_OPEN:-}" == "1" ]] || open "$URL"
        exit 0
    fi
    echo "Port $PORT drží jiný program (PID $pid). Ukonči ho, a pak spusť ./spustit.sh znovu." >&2
    exit 1
fi

# --- Prostředí: modely jen z lokální cache, žádné stahování ani telemetrie
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONUNBUFFERED=1
export GRADIO_ANALYTICS_ENABLED=False

# --- Prohlížeč otevřít, až server odpovídá (start trvá pár sekund)
if [[ "${LAB_NO_OPEN:-}" != "1" ]]; then
    (
        for _ in $(seq 1 120); do
            if curl -fs -o /dev/null --max-time 1 "$URL"; then
                open "$URL"
                exit 0
            fi
            sleep 0.5
        done
    ) &
fi

echo "Dabing lab: $URL   (ukončení Ctrl+C)"
if [[ "${LAB_NO_CAFFEINATE:-}" != "1" ]] && command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i "$PY" app.py "$@"
fi
exec "$PY" app.py "$@"
