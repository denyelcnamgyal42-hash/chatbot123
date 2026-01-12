"""Configuration settings for the WhatsApp chatbot."""
import os

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# WhatsApp Configuration
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

# WhatsApp API URL (only set if phone number ID is available)
WHATSAPP_API_URL = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages" if WHATSAPP_PHONE_NUMBER_ID else ""

# Google Sheets Configuration
GOOGLE_SHEETS_CREDENTIALS_PATH = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "credentials.json")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()

# Sheet names (configurable for flexibility) - Hotel reservations only
HOTELS_SHEET = os.getenv("HOTELS_SHEET", "hotels")
BOOKINGS_SHEET = os.getenv("BOOKINGS_SHEET", "bookings")

# Ensure sheet names are valid
ALL_SHEETS = [HOTELS_SHEET, BOOKINGS_SHEET]

# Server Configuration
PORT = int(os.getenv("PORT", 5000))
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", 5001))
DASHBOARD_AUTH_TOKEN = os.getenv("DASHBOARD_AUTH_TOKEN", "hotel-staff-2024")

# Vector Store Configuration
VECTORSTORE_PATH = os.getenv("VECTORSTORE_PATH", "vectorstore")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Model Configuration
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

# Debug Configuration
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Timeouts
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "60"))

# Max iterations for agent
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))
