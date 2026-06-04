import os

from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO


jwt = JWTManager()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[os.getenv("API_RATE_LIMIT", "120 per minute")],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)
socketio = SocketIO(
    cors_allowed_origins=[],
    async_mode=os.getenv("SOCKETIO_ASYNC_MODE", "threading"),
    logger=False,
    engineio_logger=False,
)
