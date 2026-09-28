# Dabing lab

YouTube video (nebo vlastní soubor) do češtiny: přepis, mluvčí, překlad a hlas
naklonovaný z originálu včetně emocí. Gradio na **http://127.0.0.1:7872**,
spouští se příkazem `dabing` nebo z lab-portálu (`lab` → Dabing).

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

- **Emoce**: reference hlasu je původní věta (≥ 3 s), OmniVoice převezme její přednes.
  Hlídač identity (ECAPA) věty, které hlasem vybočí, přegeneruje se stálou referencí.
- **Délka**: překlad cílí na ~90 % času originálu (OmniVoice mluví česky ~16 znaků/s).
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
- `codex_home/` — izolovaná konfigurace Codexu (auth symlink na `~/.codex/auth.json`,
  bez skills, MCP a AGENTS.md), jen předplatné, žádné API klíče.
- Modely jen z lokální HF cache (`HF_HUB_OFFLINE=1`), nic se nestahuje.

## Licence

Váhy OmniVoice jsou **CC-BY-NC** → dabing jen pro nekomerční použití.

## Po naklonování

Repozitář obsahuje jen kód. Modely, videa, zakázky ani přihlášení v něm nejsou.

```bash
uv sync                                                  # .venv (UI + podprocesy)
(cd worker/omnivoice && uv sync)                         # venv OmniVoice workeru
ln -s ~/.codex/auth.json codex_home/auth.json            # přihlášení Codexu (jen symlink)
```

Modely se berou z lokální HF cache (`HF_HUB_OFFLINE=1`). BandIt Plus patří do
`modely/bandit_plus/` a je v `.gitignore`.
