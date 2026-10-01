#!/usr/bin/env bash
# Dabing lab - stahování modelů (interaktivně, po jednom, jen s tvým souhlasem).
#
#   ./scripts/stahni_modely.sh                  projde všechny modely a u každého se zeptá [y/N]
#   ./scripts/stahni_modely.sh --list           vypíše tabulku modelů a jejich stav; nic nestahuje
#   ./scripts/stahni_modely.sh parakeet ecapa   zeptá se jen na vybrané modely
#   ./scripts/stahni_modely.sh --ano omnivoice  bez ptaní stáhne VÝSLOVNĚ jmenované modely
#                                               (jen když uživatel s tímto modelem už souhlasil)
#
# Klíče modelů: parakeet nemotron htdemucs omnivoice ecapa bandit silero
#
# Vlastnosti:
#   - Před každým stažením ukáže zdroj, velikost a licenci. Bez "y" se nestahuje nic.
#   - Idempotentní: co už je v HF cache (nebo na svém místě), se přeskočí.
#   - Modely z Hugging Face jdou do standardní HF cache (HF_HUB_CACHE, HF_HOME, jinak
#     ~/.cache/huggingface/hub), odkud je lab čte offline. Stahuje se hlavní větev (main).
#   - BandIt Plus se po stažení ověří podle SHA-256 z dabing/config.py, Silero podle pevného SHA-256.
#   - Používá HF příkaz z prostředí projektu (.venv/bin/hf); spusť po instalaci prostředí.
#
# Kompatibilní s bash 3.2 (systémový bash na macOS).

set -euo pipefail

zdroj="${BASH_SOURCE[0]}"
while [[ -L "$zdroj" ]]; do
    adresar="$(cd -P "$(dirname "$zdroj")" && pwd)"
    zdroj="$(readlink "$zdroj")"
    [[ "$zdroj" != /* ]] && zdroj="$adresar/$zdroj"
done
PROJEKT="$(cd -P "$(dirname "$zdroj")/.." && pwd)"
SKRIPT="$PROJEKT/scripts/stahni_modely.sh"
cd "$PROJEKT"

HF=".venv/bin/hf"
KLICE="parakeet nemotron htdemucs omnivoice ecapa bandit silero"
BANDIT_SOUBOR="modely/bandit_plus/model_bandit_plus_dnr_sdr_11.47.chpt"
BANDIT_URL="https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v.1.0.3/model_bandit_plus_dnr_sdr_11.47.chpt"
SILERO_SOUBOR="$HOME/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.jit"
SILERO_URL="https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.3/src/silero_vad/data/silero_vad.jit"
SILERO_SHA256="e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720"

CACHE="${HF_HUB_CACHE:-${HF_HOME:+$HF_HOME/hub}}"
CACHE="${CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface/hub}"

# --- Údaje o modelech (hodnoty: docs/INSTALACE.md; velikosti jsou dekadické, 1 GB = 10^9 B)
# Nastaví M_NAZEV M_TYP (hf|url) M_REPO M_SOUBORY (prázdné = celé repo) M_KONTROLA M_VELIKOST
# M_LICENCE M_POVINNY M_POZNAMKA M_BLOBY (SHA-256 velkých souborů = jména blobů v HF cache).
nacti_model() {
    M_NAZEV=""; M_TYP="hf"; M_REPO=""; M_SOUBORY=""; M_KONTROLA=""; M_VELIKOST=""
    M_LICENCE=""; M_POVINNY="ano"; M_POZNAMKA=""; M_BLOBY=""
    case "$1" in
    parakeet)
        M_NAZEV="Parakeet TDT 0.6B v3 (MLX) - přepis řeči"
        M_REPO="mlx-community/parakeet-tdt-0.6b-v3"
        M_SOUBORY="config.json model.safetensors"
        M_VELIKOST="2,51 GB"
        M_LICENCE="CC-BY-4.0 (vyžaduje uvedení autora, NVIDIA)"
        M_BLOBY="05e01c7f396c298cf7d23f61da7b504adeab698f0aaeafd9c82d198625464592"
        ;;
    nemotron)
        M_NAZEV="Nemotron-3-Diarization - rozlišení mluvčích"
        M_REPO="nvidia/Nemotron-3-Diarization"
        M_SOUBORY="config.json model.safetensors processor_config.json"
        M_VELIKOST="397 MB (jen 3 soubory; celé repo má 0,71 GB a nestahuje se)"
        M_LICENCE="OpenMDW-1.1 (karta modelu uvádí komerční i nekomerční použití)"
        M_BLOBY="c074d86335b3b794f8fa5edc25594558f128bdb3914d27806a3a5a2e44963cb6"
        ;;
    htdemucs)
        M_NAZEV="HTDemucs - oddělení hlasu od pozadí"
        M_REPO="adefossez/HTDemucs"
        M_SOUBORY="htdemucs.yaml 955717e8.safetensors"
        M_VELIKOST="84 MB"
        M_LICENCE="HF repo licenci neuvádí; projekt Demucs je MIT (výslovné prohlášení pro váhy neověřeno)"
        M_BLOBY="d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd"
        ;;
    omnivoice)
        M_NAZEV="OmniVoice - klonování hlasu"
        M_REPO="k2-fsa/OmniVoice"
        M_SOUBORY=""
        M_KONTROLA="config.json tokenizer.json model.safetensors audio_tokenizer/config.json audio_tokenizer/model.safetensors"
        M_VELIKOST="3,27 GB (celé repo, 13 souborů)"
        M_LICENCE="váhy CC-BY-NC = JEN NEKOMERČNÍ použití; kód Apache-2.0; audio_tokenizer/ pod Boson Higgs Audio 2 Community License"
        M_BLOBY="730839316de585f4c8298ec0e1712efc10fb19c6fa4e36eb741cb8d51ebcf6aa fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d"
        ;;
    ecapa)
        M_NAZEV="ECAPA-VoxCeleb - kontrola podobnosti hlasu"
        M_REPO="speechbrain/spkrec-ecapa-voxceleb"
        M_SOUBORY="hyperparams.yaml embedding_model.ckpt mean_var_norm_emb.ckpt classifier.ckpt label_encoder.txt"
        M_VELIKOST="89 MB"
        M_LICENCE="Apache-2.0 (podle karty; trénovací data VoxCeleb mají vlastní podmínky, neověřeno)"
        M_POVINNY="prakticky ano (kotva hlasu se vybírá podle podobnosti)"
        M_BLOBY="0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2 fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535"
        ;;
    bandit)
        M_NAZEV="BandIt Plus - oddělení dialogu od hudby a efektů"
        M_TYP="url"
        M_REPO="GitHub ZFTurbo/Music-Source-Separation-Training, release v.1.0.3"
        M_VELIKOST="148,9 MB"
        M_LICENCE="váhy odvozené od BandIt (CC-BY-NC-4.0, Zenodo 10160698) = nekomerčně; licence tohoto souboru neověřena"
        M_POVINNY="ne (bez něj jásot a smích přes řeč zmizí spolu s hlasem)"
        M_POZNAMKA="uloží se do $BANDIT_SOUBOR (složka je v .gitignore, do gitu nikdy nepatří)"
        ;;
    silero)
        M_NAZEV="Silero VAD - detekce řeči"
        M_TYP="url"
        M_REPO="GitHub snakers4/silero-vad, tag v6.2.3, soubor silero_vad.jit"
        M_VELIKOST="2,3 MB"
        M_LICENCE="MIT"
        M_POVINNY="ne (bez něj se podklad řídí jen časy slov)"
        M_POZNAMKA="uloží se do ~/.cache/torch/hub/snakers4_silero-vad_master/src/silero_vad/data/ (cesta je pevná v dabing/config.py)"
        ;;
    *)
        return 1
        ;;
    esac
    [[ -n "$M_KONTROLA" ]] || M_KONTROLA="$M_SOUBORY"
    return 0
}

# --- Pomocné funkce
sha256() { shasum -a 256 "$1" | awk '{print $1}'; }

ma_soubor() {  # existuje soubor odpovídající vzoru (i s mezerami v cestě)?
    local f
    while IFS= read -r f; do
        [[ -e "$f" ]] && return 0
    done < <(compgen -G "$1" || true)
    return 1
}

hf_adresar() { echo "$CACHE/models--${1//\//--}"; }

hf_je_stazeny() {  # všechny potřebné soubory jsou ve snapshotu a existuje refs/main
    local adr f
    adr="$(hf_adresar "$M_REPO")"
    [[ -e "$adr/refs/main" ]] || return 1
    for f in $M_KONTROLA; do
        ma_soubor "$adr/snapshots/*/$f" || return 1
    done
    return 0
}

bandit_sha_z_configu() {
    sed -n 's/^MODEL_DIALOG_SHA256 = "\([0-9a-f]\{64\}\)".*/\1/p' dabing/config.py 2>/dev/null | head -n 1
}

url_je_stazeny() {  # soubor je na místě a má správný SHA-256
    local soubor sha
    case "$1" in
    bandit) soubor="$BANDIT_SOUBOR"; sha="$(bandit_sha_z_configu)" ;;
    silero) soubor="$SILERO_SOUBOR"; sha="$SILERO_SHA256" ;;
    esac
    [[ -f "$soubor" && -n "$sha" && "$(sha256 "$soubor")" == "$sha" ]]
}

stav_modelu() {  # vypíše "stažen" nebo "chybí"
    if [[ "$M_TYP" == "hf" ]]; then
        hf_je_stazeny && echo "stažen" || echo "chybí"
    else
        url_je_stazeny "$1" && echo "stažen" || echo "chybí"
    fi
}

popis_modelu() {
    echo "[$1] $M_NAZEV"
    echo "    zdroj:    $M_REPO"
    if [[ "$M_TYP" == "hf" && -n "$M_SOUBORY" ]]; then
        echo "    soubory:  $M_SOUBORY"
    elif [[ "$M_TYP" == "hf" ]]; then
        echo "    soubory:  celý repozitář (včetně podsložky audio_tokenizer/)"
    fi
    echo "    velikost: $M_VELIKOST"
    echo "    licence:  $M_LICENCE"
    echo "    povinný:  $M_POVINNY"
    [[ -z "$M_POZNAMKA" ]] || echo "    poznámka: $M_POZNAMKA"
}

pouziti() {
    sed -n '2,/^$/p' "$SKRIPT" | sed 's/^# \{0,1\}//'
}

# --- Argumenty
LIST=0
ANO=0
VYBER=""
for a in "$@"; do
    case "$a" in
    --list) LIST=1 ;;
    --ano) ANO=1 ;;
    -h | --help) pouziti; exit 0 ;;
    -*) echo "Neznámý přepínač: $a (viz --help)" >&2; exit 2 ;;
    *)
        nacti_model "$a" || { echo "Neznámý model: $a (platné: $KLICE)" >&2; exit 2; }
        VYBER="$VYBER $a"
        ;;
    esac
done
if [[ $ANO -eq 1 && -z "$VYBER" ]]; then
    echo "Přepínač --ano vyžaduje výslovně jmenovat modely, např.: --ano parakeet ecapa" >&2
    exit 2
fi
[[ -n "$VYBER" ]] || VYBER="$KLICE"

# --- Výpis
if [[ $LIST -eq 1 ]]; then
    echo "Modely Dabing labu (nic se nestahuje). HF cache: $CACHE"
    echo
    for k in $VYBER; do
        nacti_model "$k"
        popis_modelu "$k"
        echo "    stav:     $(stav_modelu "$k")"
        echo
    done
    echo "Celkem zhruba 6,5 GB (povinné modely 6,35 GB). Stažení: ./scripts/stahni_modely.sh"
    exit 0
fi

# --- Příprava ke stažení
if [[ ! -x "$HF" ]]; then
    echo "CHYBA: chybí $HF - nejdřív nainstaluj prostředí projektu (docs/INSTALACE.md, krok Python prostředí)." >&2
    exit 1
fi
if [[ $ANO -eq 0 && ! -t 0 ]]; then
    echo "Tohle není interaktivní terminál, takže se není koho zeptat a nic se nestahuje." >&2
    echo "Spusť skript ve vlastním terminálu, nebo po souhlasu uživatele: --ano <model> ..." >&2
    exit 2
fi
if [[ -n "${HF_HOME:-}${HF_HUB_CACHE:-}" ]]; then
    echo "Pozor: HF_HOME nebo HF_HUB_CACHE je nastavené, modely půjdou do: $CACHE"
    echo "       Lab musí běžet se stejnou proměnnou, jinak modely nenajde."
fi
echo "Volné místo: $(df -h . | awk 'NR==2 {print $4}') (na všechny modely je potřeba zhruba 6,5 GB)."
echo

zeptej_se() {  # $1 = klíč; vrací 0 = ano
    local odp
    if [[ $ANO -eq 1 ]]; then
        echo "Souhlas zadán přepínačem --ano."
        return 0
    fi
    while true; do
        printf 'Stáhnout? [y/N, d = jen ukázat soubory a velikost] '
        read -r odp || odp="n"
        case "$odp" in
        y | Y | ano | ANO | Ano) return 0 ;;
        d | D)
            if [[ "$M_TYP" == "hf" ]]; then
                # shellcheck disable=SC2086
                env -u HF_HUB_OFFLINE -u TRANSFORMERS_OFFLINE "$HF" download "$M_REPO" $M_SOUBORY --dry-run || true
            else
                echo "    (soubor: $M_REPO; velikost $M_VELIKOST)"
            fi
            ;;
        *) return 1 ;;
        esac
    done
}

stahni_hf() {
    local adr b
    # HF_HUB_OFFLINE musí být vypnuté, jinak se nic nestáhne. Žádné --revision ani --local-dir:
    # lab čte jen standardní cache s refs/main.
    # shellcheck disable=SC2086
    env -u HF_HUB_OFFLINE -u TRANSFORMERS_OFFLINE "$HF" download "$M_REPO" $M_SOUBORY || return 1
    hf_je_stazeny || { echo "CHYBA: po stažení v cache pořád něco chybí ($M_KONTROLA)." >&2; return 1; }
    adr="$(hf_adresar "$M_REPO")"
    for b in $M_BLOBY; do
        [[ -e "$adr/blobs/$b" ]] \
            || echo "Pozor: velký soubor s očekávaným SHA-256 ${b:0:12}... v cache není (na HF mohla vyjít novější revize; ověř, že lab model načte)." >&2
    done
    return 0
}

stahni_url() {  # $1 = klíč
    local cil url sha ocekavany cast
    case "$1" in
    bandit)
        cil="$BANDIT_SOUBOR"; url="$BANDIT_URL"; ocekavany="$(bandit_sha_z_configu)"
        [[ -n "$ocekavany" ]] || { echo "CHYBA: v dabing/config.py jsem nenašel MODEL_DIALOG_SHA256." >&2; return 1; }
        ;;
    silero)
        cil="$SILERO_SOUBOR"; url="$SILERO_URL"; ocekavany="$SILERO_SHA256"
        ;;
    esac
    mkdir -p "$(dirname "$cil")"
    cast="$cil.part"
    if ! curl -fL --retry 3 --progress-bar -o "$cast" "$url"; then
        rm -f "$cast"
        echo "CHYBA: stažení z $url selhalo." >&2
        return 1
    fi
    sha="$(sha256 "$cast")"
    if [[ "$sha" != "$ocekavany" ]]; then
        rm -f "$cast"
        echo "CHYBA: SHA-256 nesedí (dostal jsem $sha, čekal $ocekavany). Soubor jsem smazal, zkus to znovu." >&2
        return 1
    fi
    mv "$cast" "$cil"
    echo "SHA-256 sedí."
    return 0
}

# --- Hlavní smyčka
stazeno=0; preskoceno=0; selhalo=0; odmitnuto=0
for k in $VYBER; do
    nacti_model "$k"
    popis_modelu "$k"
    if [[ "$(stav_modelu "$k")" == "stažen" ]]; then
        echo "    -> už je stažený, přeskakuji."
        echo
        preskoceno=$((preskoceno + 1))
        continue
    fi
    if [[ "$k" == "bandit" && -f "$BANDIT_SOUBOR" ]] || [[ "$k" == "silero" && -f "$SILERO_SOUBOR" ]]; then
        echo "    Pozor: soubor už existuje, ale SHA-256 nesedí. Stažením se přepíše."
    fi
    if ! zeptej_se "$k"; then
        echo "    -> přeskočeno (bez souhlasu se nestahuje)."
        echo
        odmitnuto=$((odmitnuto + 1))
        continue
    fi
    if [[ "$M_TYP" == "hf" ]]; then
        stahni_hf && vysl=0 || vysl=1
    else
        stahni_url "$k" && vysl=0 || vysl=1
    fi
    if [[ $vysl -eq 0 ]]; then
        echo "    -> hotovo."
        stazeno=$((stazeno + 1))
    else
        echo "    -> SELHALO. Nápověda: zkontroluj připojení a volné místo; když stahování váhá, zkus HF_HUB_DISABLE_XET=1 ./scripts/stahni_modely.sh $k" >&2
        selhalo=$((selhalo + 1))
    fi
    echo
done

echo "Souhrn: staženo $stazeno, už bylo hotové $preskoceno, přeskočeno bez souhlasu $odmitnuto, selhalo $selhalo."
echo "Ověření celé instalace: ./spustit.sh --kontrola"
[[ $selhalo -eq 0 ]]
