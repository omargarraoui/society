"""Configurazione del mondo. Tutto cio' che e' un limite o una regola sta qui, non nei prompt."""
import os

DB_PATH = os.environ.get("SOCIETY_DB", "society.db")

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

# --- Tempo del mondo: 1 tick = 15 minuti di orologio del mondo ---
TICKS_PER_DAY = 96
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7
NIGHT_AWAKE_PROB = 0.10   # probabilita' per agente di restare sveglio di notte
NIGHT_AWAKE_MAX = 1       # tetto di svegli per tick di notte
TICK_SECONDS = 900        # pausa reale tra i tick (0 = massima velocita', per test)

# --- Limiti duri (non modificabili dagli agenti ne' dall'agente-Dio) ---
MAX_AGENTS = 12
START_FOOD = 5
UPKEEP_PER_DAY = 1
WORK_GAIN = 2
STARVE_BELOW = -2         # sotto questa soglia di cibo l'agente muore
MIN_FOOD_FOR_BIRTH = 3    # cibo combinato minimo della coppia

# --- Fondatori: (nome, sesso, modello nascosto) ---
FOUNDERS = [("Adam", "M", "groq"), ("Eve", "F", "gemini")]
GOD_MODEL = "cloudflare"  # il modello dell'agente-Dio e' separato da quello dei gruppi

NAMES_M = ["Seth", "Enos", "Cain", "Abel", "Noah", "Enoch", "Jared", "Lamech", "Eber", "Terah"]
NAMES_F = ["Adah", "Zillah", "Naamah", "Milcah", "Sarai", "Hagar", "Leah", "Rachel", "Dinah", "Tamar"]

# --- Provider LLM (formato OpenAI-compatibile). VERIFICA modelli e limiti: cambiano spesso. ---
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_env": "GROQ_API_KEY",
        "daily_cap": 600,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
        "key_env": "GEMINI_API_KEY",
        "daily_cap": 400,
    },
    "cloudflare": {
        # imposta CLOUDFLARE_ACCOUNT_ID oltre a CLOUDFLARE_API_TOKEN
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "key_env": "CLOUDFLARE_API_TOKEN",
        "daily_cap": 200,
    },
}
