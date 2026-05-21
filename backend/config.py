import os

from dotenv import load_dotenv


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


def _get_env(name, fallback=None):
    value = os.getenv(name)
    if value is None:
        return fallback
    return value.strip()


def _get_bool_env(name, fallback=False):
    value = _get_env(name)
    if value is None:
        return fallback
    return value.lower() in {"1", "true", "yes", "on"}


def get_cors_origins():
    raw_value = _get_env("CORS_ORIGINS") or _get_env("FRONTEND_ORIGIN")

    if not raw_value:
        return ["http://32.197.44.5:3000"]

    origins = [origin.strip() for origin in raw_value.split(",") if origin.strip()]
    return origins or ["http://32.197.44.5:3000"]


class ProductionConfig:
    SECRET_KEY = _get_env("SECRET_KEY", "change-me-in-production")
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = int(_get_env("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _get_bool_env("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = _get_env("SESSION_COOKIE_SAMESITE", "Lax")
    PREFERRED_URL_SCHEME = _get_env("PREFERRED_URL_SCHEME", "https")
