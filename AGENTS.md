# Pokyny pro AI agenty (Codex a další)

Dabing lab je místní aplikace pro macOS s Apple Silicon. Instalaci dělá `./instalovat.sh` (idempotentní, slouží i jako aktualizace; nejdřív
`--zkouska`). Pro **instalaci, spuštění, aktualizaci
a řešení potíží** postupuj podle `.claude/skills/dabing-lab/SKILL.md` (playbook pro agenta) a
`docs/INSTALACE.md` (úplný návod). Komunikuj s uživatelem česky.

Pravidla: před každým velkým stažením se zeptej (repo, velikost, licence), nikdy nečti ani
nevypisuj `codex_home/auth.json` a tokeny, přihlášení ke Codexu se dělá v UI labu, žádné `sudo`
bez souhlasu uživatele. Interaktivní příkazy (heslo, `./instalovat.sh`, `./scripts/stahni_modely.sh`)
spouští uživatel v samostatném okně Terminálu, ne ty. Váhy OmniVoice jsou CC-BY-NC, tedy jen
nekomerční použití. Klonuje se hlas každého mluvčího ve videu, souhlas je potřeba od všech.
Překladač (Codex, Ollama, Bionic / LM Studio) a jeho model volí uživatel v aplikaci podle paměti svého Macu;
žádný model nedoporučuj ani neinstaluj.
