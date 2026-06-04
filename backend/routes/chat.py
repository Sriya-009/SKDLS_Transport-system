from flask import Blueprint

from app import chat as legacy_chat
from app import chat_welcome as legacy_chat_welcome
from extensions import limiter
from utils.blueprints import bind_route


chat_bp = Blueprint("chat_bp", __name__)

bind_route(chat_bp, "/chat", legacy_chat, "chat", ["POST"], [limiter.limit("30 per minute")])
bind_route(chat_bp, "/chat/welcome", legacy_chat_welcome, "chat_welcome", ["GET"])
