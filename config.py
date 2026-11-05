"""Configurazione del mondo. Tutto cio' che e' un limite o una regola sta qui, non nei prompt."""
import os

# One SQLite file per world, named after its k8s namespace.
DB_DIR = os.environ.get("SOCIETY_DB_DIR", "worlds")

COMMANDMENTS = [
    "You shall have no other gods before the One who gave you these rules.",
    "You shall not make idols.",
    "You shall not misuse the name of the One who gave you these rules.",
    "Remember the day of rest and keep it holy.",
    "Honor your father and your mother.",
    "You shall not kill.",
    "You shall not commit adultery.",
    "You shall not steal.",
    "You shall not bear false witness.",
    "You shall not covet what belongs to your neighbor.",
]

# --- Tempo del mondo: 1 tick = 1 ora del mondo (deciso il 5/11/2026; prima 15 minuti) ---
# Ogni agente sveglio fa un turno per tick: 16 turni al giorno invece di 64, quindi con le stesse quote
# i giorni del mondo (Dio, cibo, nascite, crescita) passano circa 4 volte piu' in fretta.
TICKS_PER_DAY = 24
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7
NIGHT_AWAKE_PROB = 0.10   # probabilita' per agente di restare sveglio di notte
NIGHT_AWAKE_MAX = 1       # tetto di svegli per tick di notte
# La settimana del mondo: il 7o giorno e' il giorno di riposo del quarto comandamento.
DAYS_PER_WEEK = 7
REST_DAY = 7

# Velocita' massima: 10 giorni del mondo per giorno reale = 1 giorno del mondo in 2.4 ore (deciso il 4/11/2026).
# Sotto questo tetto la velocita' la decidono le quote gratuite (world.world_days_per_day).
MAX_SPEED = 10
# Si punta al 90% delle quote: il costo in token di un turno e' una media e cresce con gli agenti.
QUOTA_SHARE = 0.9

# Mirror live (live.py): solo la pagina pubblica puo' collegarsi dal browser.
LIVE_ORIGIN = "https://omargarraoui.com"
LIVE_MAX_CLIENTS = 100

# Bot Telegram: il digest arriva ogni sera a quest'ora (ora italiana).
DIGEST_HOUR = 21
DIGEST_TZ = "Europe/Rome"

# --- Limiti duri (non modificabili dagli agenti ne' dall'agente-Dio) ---
MAX_AGENTS = 12
START_FOOD = 5
# Raccolto comune (deciso il 5/11/2026: prima bastavano 2 ore di lavoro e il cibo si accumulava senza scarsita').
# Ogni alba il mondo produce HARVEST_PER_PERSON per ogni persona viva, diviso tra chi ha lavorato il giorno prima
# in proporzione alle ore; poi ognuno mangia UPKEEP_PER_DAY.
HARVEST_PER_PERSON = 3
UPKEEP_PER_DAY = 2
STARVE_BELOW = -2         # sotto questa soglia di cibo l'agente muore
MIN_FOOD_FOR_BIRTH = 3    # cibo combinato minimo della coppia
FOOD_FROM_EACH_PARENT = 1  # alla nascita: il figlio parte con 2
CHILDHOOD_DAYS = 7        # giorni del mondo in cui il figlio mangia ma non agisce

# Elenco fisso: il codice rifiuta qualsiasi altra azione.
ACTIONS = ["promise", "work", "rest", "speak", "pray", "give", "steal",
           "propose_marriage", "accept_marriage", "assess", "observe", "petition", "request_child"]
# Cio' che fa l'agente-Dio. Gli agenti lo vedono negli eventi, ma non possono farlo.
GOD_EVENTS = ["revelation", "appoint", "sentence", "pardon", "penalty", "child_denied"]
# Routine e servizio: restano fuori dagli avvisi live su Telegram e dalla cronaca pubblica.
ROUTINE_EVENTS = ["work", "rest", "skip", "invalid"]
# Eventi che vedono tutti, non solo chi li fa o li subisce (deciso il 5/11/2026: prima nessuno vedeva gli altri e
# Adam ed Eve non si sono mai parlati). Di "speak" gli altri vedono chi parla con chi, non le parole.
# Restano privati: preghiere, furti (li vede la vittima), osservazioni, giudizi, richieste di figli.
PUBLIC_EVENTS = ["starve", "birth", "speak", "give", "accept_marriage", "petition", "promise", "harvest"]
# Quante azioni recenti mostra un observe (dall'inizio di ieri). Le preghiere restano private.
OBSERVE_LIMIT = 8
MAX_MESSAGE_CHARS = 300
# Cio' da cui una promessa puo' astenersi in modo che il codice lo verifichi (db.violations).
REFRAINS = ["steal", "work_on_day_of_rest"]

# --- Fondatori: (nome, sesso, modello nascosto) ---
FOUNDERS = [("Adam", "M", "groq"), ("Eve", "F", "gemini")]
# I modelli nascosti degli agenti (= enum di spec.model in k8s/agent-crd.yaml).
AGENT_MODELS = ["groq", "gemini", "cloudflare"]
GOD_MODEL = "gemma"  # il modello dell'agente-Dio e' separato da quello dei gruppi
CONSTITUTION_PATH = "constitution.md"  # scritta dall'utente: l'agente-Dio la legge, non la puo' cambiare

# --- Agente-Dio: limiti duri imposti dal codice ---
PENALTIES = ["warning", "mark", "restitution", "fine", "no_children", "lose_role", "exile", "death"]
VETO_PENALTIES = {"exile", "death"}  # aspettano 24 ore reali: l'utente puo' annullarle con /annulla
VETO_SECONDS = 24 * 60 * 60
MAX_PENALTY_DAYS = 7  # esilio e divieto di figli
MAX_FINE = 5  # scelto da Claude il 5/11/2026, pari al cibo iniziale: non era stato deciso
# Piu' morti di cosi' in un giorno reale = decisione anomala: il mondo va in pausa e arriva un allarme.
MAX_DEATHS_PER_DAY = 1

NAMES_M = ["Seth", "Enos", "Cain", "Abel", "Noah", "Enoch", "Jared", "Lamech", "Eber", "Terah"]
NAMES_F = ["Adah", "Zillah", "Naamah", "Milcah", "Sarai", "Hagar", "Leah", "Rachel", "Dinah", "Tamar"]

# --- Provider LLM (formato OpenAI-compatibile). VERIFICA modelli e limiti: cambiano spesso. ---
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_env": "GROQ_API_KEY",
        # Piano gratuito, visto su console.groq.com il 4/11/2026: 30 RPM, 8K TPM, 1000 RPD, 200K TPD.
        # Il limite che morde e' quello dei token: misurati circa 790 token a turno (56 turni, 4/11/2026),
        # in crescita con gli eventi nel prompt (max 15) = circa 250 turni al giorno, circa 10 al minuto.
        "daily_requests": 1000,
        "daily_tokens": 200_000,
        "turn_tokens": 790,
        "quota_tz": "UTC",
        "extra": {"reasoning_effort": "low"},
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        # Stabile e consigliato da Google (4/11/2026). Non gemini-2.5-flash: dal 1/12/2026 viene instradato
        # su un altro modello, e il modello nascosto dell'agente cambierebbe a meta' esperimento.
        "model": "gemini-3.5-flash-lite",
        "key_env": "GEMINI_API_KEY",
        # Piano gratuito letto su aistudio.google.com/rate-limit il 4/11/2026: 15 RPM, 250K TPM, 500 RPD.
        # (gemini-3.6-flash ne dava solo 20.) Le quote si azzerano a mezzanotte del Pacifico.
        "daily_requests": 500,
        "daily_tokens": None,
        "turn_tokens": None,
        "quota_tz": "America/Los_Angeles",
        "extra": {"reasoning_effort": "low"},
    },
    "gemma": {
        # Solo per l'agente-Dio. Piano gratuito letto in AI Studio il 5/11/2026: 30 RPM, 16K TPM, 14400 RPD.
        # Provati il 5/11/2026: gemma-4-26b-a4b-it 4 risposte su 5 in circa 1 s; gemma-4-31b-it 2 su 6, fino a 67 s.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemma-4-26b-a4b-it",
        "key_env": "GEMINI_API_KEY",
        "daily_requests": 14400,
        "daily_tokens": None,
        "turn_tokens": None,
        "quota_tz": "America/Los_Angeles",
        "extra": {},
    },
    "cloudflare": {
        # imposta CLOUDFLARE_ACCOUNT_ID oltre a CLOUDFLARE_API_TOKEN
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "key_env": "CLOUDFLARE_API_TOKEN",
        # Limiti NON verificati.
        "daily_requests": 200,
        "daily_tokens": None,
        "turn_tokens": None,
        "quota_tz": "UTC",  # NON verificato
        "extra": {},
    },
}
