import os
import re
from datetime import timedelta

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(__file__)
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)


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


def validate_production_environment():
    errors = []

    secret_key = _get_env("SECRET_KEY")
    jwt_secret_key = _get_env("JWT_SECRET_KEY")
    cors_origins = _get_env("CORS_ORIGINS")
    flask_env = _get_env("FLASK_ENV", "production").lower()

    if flask_env == "production":
        if not secret_key or secret_key == "change-me-in-production":
            errors.append("SECRET_KEY must be set to a secure random value")

        if jwt_secret_key == "change-me-in-production":
            errors.append("JWT_SECRET_KEY must not use the default placeholder")

        if not cors_origins:
            errors.append("CORS_ORIGINS must list the allowed frontend origins")

    if errors:
        raise RuntimeError("Production environment validation failed: " + "; ".join(errors))

    return True


def get_cors_origins():
    raw_value = _get_env("CORS_ORIGINS") or _get_env("FRONTEND_ORIGIN")

    if not raw_value:
        return ["http://34.200.212.3", "http://127.0.0.1:5173", "http://localhost:5173"]

    origins = [match.strip() for match in re.findall(r"https?://[^\s,\]\)]+", raw_value)]
    if not origins:
        origins = [origin.strip().strip("[]()") for origin in raw_value.strip("[]").split(",") if origin.strip()]

    return list(dict.fromkeys(origins)) or ["http://34.200.212.3", "http://127.0.0.1:5173", "http://localhost:5173"]


class ProductionConfig:
    SECRET_KEY = _get_env("SECRET_KEY", "dev-secret-key")
    JWT_SECRET_KEY = _get_env("JWT_SECRET_KEY", SECRET_KEY)
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=int(_get_env("JWT_ACCESS_TOKEN_EXPIRES_DAYS", 7)))
    JWT_COOKIE_SECURE = _get_bool_env("JWT_COOKIE_SECURE", _get_bool_env("SESSION_COOKIE_SECURE", False))
    JWT_COOKIE_SAMESITE = _get_env("JWT_COOKIE_SAMESITE", _get_env("SESSION_COOKIE_SAMESITE", "Lax"))
    JWT_COOKIE_CSRF_PROTECT = _get_bool_env("JWT_COOKIE_CSRF_PROTECT", True)
    JWT_COOKIE_CSRF_HEADER_NAME = _get_env("JWT_COOKIE_CSRF_HEADER_NAME", "X-CSRF-TOKEN")
    JWT_ACCESS_COOKIE_PATH = _get_env("JWT_ACCESS_COOKIE_PATH", "/")
    JWT_REFRESH_COOKIE_PATH = _get_env("JWT_REFRESH_COOKIE_PATH", "/")
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = int(_get_env("MAX_CONTENT_LENGTH", 5 * 1024 * 1024))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _get_bool_env("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = _get_env("SESSION_COOKIE_SAMESITE", "Lax")
    PREFERRED_URL_SCHEME = _get_env("PREFERRED_URL_SCHEME", "http")
