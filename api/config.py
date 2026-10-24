"""All config comes from env vars (see .env.example)."""
import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://tenderhawk:tenderhawk@localhost:5432/tenderhawk")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def fallback_mode() -> bool:
    return os.getenv("FALLBACK_MODE", "false").lower() in ("1", "true", "yes")
