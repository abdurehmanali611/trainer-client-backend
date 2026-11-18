import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = env("SECRET_KEY", env("DJANGO_SECRET_KEY", "insecure-dev-key"))
# Shared with trainer/BackEnd/.env — client JWTs verify across both services.
JWT_SECRET = env("JWT_SECRET", SECRET_KEY)

DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "*").split(",") if h.strip()]

# Standalone client API — each app serves its own feature area directly
# against the shared MySQL database (no proxy to the trainer backend).
INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
    "apps.core",
    "apps.auth",
    "apps.clients",
    "apps.plans",
    "apps.schedule",
    "apps.billing",
    "apps.chat",
    "apps.journal",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Addis_Ababa"
USE_TZ = True

# MySQL (individual env vars take priority; SSL is enabled automatically for
# non-local hosts). Same database as the trainer backend — the trainer owns
# the schema (migrations); this service only reads/writes those tables.
DB_NAME = env("DB_NAME")
if DB_NAME:
    db_host = env("DB_HOST", "127.0.0.1")
    db_options: dict = {"charset": "utf8mb4"}
    if env_bool("DB_SSL", db_host not in ("localhost", "127.0.0.1", "::1")):
        db_options["ssl"] = {}
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": DB_NAME,
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": db_host,
            "PORT": env("DB_PORT", "3306"),
            "OPTIONS": db_options,
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        }
    }
elif env("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.config(default=env("DATABASE_URL"), conn_max_age=600)}
else:
    raise RuntimeError(
        "No database configured. Set DB_NAME (and DB_USER/DB_PASSWORD/DB_HOST/DB_PORT) "
        "or DATABASE_URL in BackEnd/.env before starting the client backend."
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Token auth only — every endpoint resolves the client from the Bearer JWT
# (or is public, like login). django.contrib.auth is deliberately not installed.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (),
    "DEFAULT_PERMISSION_CLASSES": (),
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    # Feature 15: keeps `blocked` a real boolean in the write-gate 403.
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
}

# The mobile/web client calls this backend cross-origin in development.
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", DEBUG)
CORS_ALLOWED_ORIGINS = [
    origin.strip() for origin in env("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()
]

# ---------------------------------------------------------------------------
# Email — console backend (prints to runserver console) when EMAIL_HOST empty
# ---------------------------------------------------------------------------
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Trainer <noreply@example.com>")

# Frontend deep-link base used in reset emails
CLIENT_APP_RESET_URL = env("CLIENT_APP_RESET_URL", "trainerclient://auth/reset")
