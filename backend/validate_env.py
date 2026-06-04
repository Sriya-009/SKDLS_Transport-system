import os
import sys

from dotenv import load_dotenv


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

REQUIRED = [
    'DB_HOST',
    'DB_USER',
    'DB_PASSWORD',
    'DB_NAME',
    'DB_PORT',
    'SECRET_KEY',
    'JWT_SECRET_KEY',
    'FRONTEND_ORIGIN',
    'CORS_ORIGINS',
]

OPTIONAL = [
    'GEMINI_API_KEY',
    'GEMINI_MODEL_NAME',
    'SOCKETIO_ASYNC_MODE',
]


def main():
    missing = []
    for key in REQUIRED:
        if not os.getenv(key):
            missing.append(key)

    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    # Warn about optional providers
    optional_missing = [k for k in OPTIONAL if not os.getenv(k)]
    if optional_missing:
        print(f"Warning: Optional LLM provider keys not set: {', '.join(optional_missing)}")

    try:
        int(os.getenv('DB_PORT', '3306'))
    except ValueError:
        print('DB_PORT must be an integer', file=sys.stderr)
        sys.exit(2)

    print('Environment validation passed')


if __name__ == '__main__':
    main()
