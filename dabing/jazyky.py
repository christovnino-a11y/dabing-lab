"""Jazyky: z čeho se dabuje (originál) a do čeho (dabing).

Originál: jen jazyky, které umí přepis Parakeet TDT v3 (25 evropských jazyků).
Dabing: jazyky s latinkou, cyrilicí nebo řečtinou, které umí OmniVoice (600+ jazyků)
a zároveň je umí Parakeet — ten zpětným přepisem kontroluje srozumitelnost dabingu.
Každý jazyk dabingu má „kotvu": pevnou větu, kterou klon mluvčího namluví jako první
a z níž se pak klonují všechny jeho věty (výslovnost bez přízvuku originálu, viz tts.py).
"""

from __future__ import annotations

from typing import NamedTuple


class JazykDabingu(NamedTuple):
    nazev: str        # v nabídce
    do_jazyka: str    # pro zadání překladateli: „… do {do_jazyka}"
    iso3: str         # ISO 639-2 pro stopu v MP4
    stopa: str        # název zvukové stopy v přehrávači
    kotva: str        # pevná věta pro zamčení hlasu (význam jako česká verze)


DABING: dict[str, JazykDabingu] = {
    "cs": JazykDabingu("Čeština", "češtiny", "ces", "Čeština (dabing)",
                       "Dobrý den a vítejte. Dnes vám chci ukázat, na čem jsme celý poslední rok "
                       "pracovali, a upřímně si myslím, že vás to opravdu překvapí."),
    "sk": JazykDabingu("Slovenština", "slovenštiny", "slk", "Slovenčina (dabing)",
                       "Dobrý deň a vitajte. Dnes vám chcem ukázať, na čom sme celý posledný rok "
                       "pracovali, a úprimne si myslím, že vás to naozaj prekvapí."),
    "en": JazykDabingu("Angličtina", "angličtiny", "eng", "English (dub)",
                       "Hello and welcome. Today I want to show you what we have been working on "
                       "for the whole past year, and I honestly think it will really surprise you."),
    "de": JazykDabingu("Němčina", "němčiny", "deu", "Deutsch (Synchronfassung)",
                       "Guten Tag und herzlich willkommen. Heute möchte ich Ihnen zeigen, woran wir "
                       "im letzten Jahr gearbeitet haben, und ich glaube ehrlich, dass es Sie "
                       "überraschen wird."),
    "pl": JazykDabingu("Polština", "polštiny", "pol", "Polski (dubbing)",
                       "Dzień dobry i witajcie. Dziś chcę wam pokazać, nad czym pracowaliśmy przez "
                       "cały ostatni rok, i szczerze mówiąc myślę, że naprawdę was to zaskoczy."),
    "fr": JazykDabingu("Francouzština", "francouzštiny", "fra", "Français (doublage)",
                       "Bonjour et bienvenue. Aujourd'hui, je veux vous montrer ce sur quoi nous "
                       "avons travaillé toute l'année dernière, et honnêtement, je pense que cela "
                       "va vraiment vous surprendre."),
    "es": JazykDabingu("Španělština", "španělštiny", "spa", "Español (doblaje)",
                       "Hola y bienvenidos. Hoy quiero mostrarles en qué hemos trabajado durante "
                       "todo el último año, y sinceramente creo que realmente los va a sorprender."),
    "it": JazykDabingu("Italština", "italštiny", "ita", "Italiano (doppiaggio)",
                       "Buongiorno e benvenuti. Oggi voglio mostrarvi ciò su cui abbiamo lavorato "
                       "per tutto l'ultimo anno, e sinceramente penso che vi sorprenderà davvero."),
    "pt": JazykDabingu("Portugalština", "portugalštiny", "por", "Português (dobragem)",
                       "Olá e bem-vindos. Hoje quero mostrar-vos aquilo em que estivemos a "
                       "trabalhar durante todo o último ano, e sinceramente acho que vos vai "
                       "mesmo surpreender."),
    "nl": JazykDabingu("Nizozemština", "nizozemštiny", "nld", "Nederlands (nasynchronisatie)",
                       "Goedendag en welkom. Vandaag wil ik jullie laten zien waar we het hele "
                       "afgelopen jaar aan hebben gewerkt, en eerlijk gezegd denk ik dat het "
                       "jullie echt zal verrassen."),
    "hu": JazykDabingu("Maďarština", "maďarštiny", "hun", "Magyar (szinkron)",
                       "Jó napot, és szeretettel köszöntöm önöket. Ma meg szeretném mutatni, min "
                       "dolgoztunk az elmúlt egy évben, és őszintén szólva azt hiszem, ez tényleg "
                       "meg fogja lepni önöket."),
    "uk": JazykDabingu("Ukrajinština", "ukrajinštiny", "ukr", "Українська (дубляж)",
                       "Добрий день і ласкаво просимо. Сьогодні я хочу показати вам, над чим ми "
                       "працювали весь минулий рік, і, чесно кажучи, думаю, що це вас справді "
                       "здивує."),
    "ru": JazykDabingu("Ruština", "ruštiny", "rus", "Русский (дубляж)",
                       "Добрый день и добро пожаловать. Сегодня я хочу показать вам, над чем мы "
                       "работали весь прошлый год, и, честно говоря, думаю, что это вас "
                       "действительно удивит."),
    "hr": JazykDabingu("Chorvatština", "chorvatštiny", "hrv", "Hrvatski (sinkronizacija)",
                       "Dobar dan i dobrodošli. Danas vam želim pokazati na čemu smo radili cijelu "
                       "prošlu godinu i iskreno mislim da će vas to stvarno iznenaditi."),
    "sl": JazykDabingu("Slovinština", "slovinštiny", "slv", "Slovenščina (sinhronizacija)",
                       "Dober dan in dobrodošli. Danes vam želim pokazati, na čem smo delali vse "
                       "zadnje leto, in iskreno mislim, da vas bo to res presenetilo."),
    "ro": JazykDabingu("Rumunština", "rumunštiny", "ron", "Română (dublaj)",
                       "Bună ziua și bine ați venit. Astăzi vreau să vă arăt la ce am lucrat pe "
                       "parcursul întregului an trecut și, sincer, cred că vă va surprinde cu "
                       "adevărat."),
    "bg": JazykDabingu("Bulharština", "bulharštiny", "bul", "Български (дублаж)",
                       "Добър ден и добре дошли. Днес искам да ви покажа върху какво работихме "
                       "през цялата изминала година и, честно казано, мисля, че наистина ще ви "
                       "изненада."),
    "el": JazykDabingu("Řečtina", "řečtiny", "ell", "Ελληνικά (μεταγλώττιση)",
                       "Καλημέρα και καλώς ήρθατε. Σήμερα θέλω να σας δείξω πάνω σε τι δουλεύαμε "
                       "όλο τον τελευταίο χρόνο, και ειλικρινά πιστεύω ότι θα σας εκπλήξει "
                       "πραγματικά."),
    "sv": JazykDabingu("Švédština", "švédštiny", "swe", "Svenska (dubbning)",
                       "Hej och välkomna. I dag vill jag visa er vad vi har arbetat med under hela "
                       "det senaste året, och ärligt talat tror jag att det verkligen kommer att "
                       "överraska er."),
    "da": JazykDabingu("Dánština", "dánštiny", "dan", "Dansk (eftersynkronisering)",
                       "Goddag og velkommen. I dag vil jeg vise jer, hvad vi har arbejdet på hele "
                       "det seneste år, og ærligt talt tror jeg, at det virkelig vil overraske jer."),
    "fi": JazykDabingu("Finština", "finštiny", "fin", "Suomi (jälkiäänitys)",
                       "Hyvää päivää ja tervetuloa. Tänään haluan näyttää teille, mitä olemme "
                       "tehneet koko viime vuoden, ja rehellisesti sanottuna uskon, että se "
                       "todella yllättää teidät."),
}

# Originál: jazyky přepisu Parakeet TDT v3 (kód → název v nabídce, „z …" pro překladač).
ORIGINAL: dict[str, tuple[str, str]] = {
    "auto": ("Automaticky", "z jazyka originálu (rozpoznej ho sám)"),
    "en": ("Angličtina", "z angličtiny"),
    "de": ("Němčina", "z němčiny"),
    "fr": ("Francouzština", "z francouzštiny"),
    "es": ("Španělština", "ze španělštiny"),
    "it": ("Italština", "z italštiny"),
    "pl": ("Polština", "z polštiny"),
    "sk": ("Slovenština", "ze slovenštiny"),
    "cs": ("Čeština", "z češtiny"),
    "ru": ("Ruština", "z ruštiny"),
    "uk": ("Ukrajinština", "z ukrajinštiny"),
    "pt": ("Portugalština", "z portugalštiny"),
    "nl": ("Nizozemština", "z nizozemštiny"),
    "hu": ("Maďarština", "z maďarštiny"),
    "ro": ("Rumunština", "z rumunštiny"),
    "hr": ("Chorvatština", "z chorvatštiny"),
    "sl": ("Slovinština", "ze slovinštiny"),
    "bg": ("Bulharština", "z bulharštiny"),
    "el": ("Řečtina", "z řečtiny"),
    "sv": ("Švédština", "ze švédštiny"),
    "da": ("Dánština", "z dánštiny"),
    "fi": ("Finština", "z finštiny"),
    "et": ("Estonština", "z estonštiny"),
    "lv": ("Lotyština", "z lotyštiny"),
    "lt": ("Litevština", "z litevštiny"),
    "mt": ("Maltština", "z maltštiny"),
}
