import os

from flask import Flask
from flask_cors import CORS

from app import (
    ALLOWED_CORS_ORIGINS,
    ProductionConfig,
    _monitoring_after_request as legacy_monitoring_after_request,
    _monitoring_before_request as legacy_monitoring_before_request,
)
from extensions import jwt, limiter, socketio
from monitoring.hooks import register_monitoring_hooks
from monitoring.routes import monitoring_bp
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.bookings import bookings_bp
from routes.chat import chat_bp
from routes.gps import gps_bp
from routes.payments import payments_bp
from routes.search import search_bp
from routes.shipments import shipments_bp
from routes.system import system_bp
from sockets.tracking import register_socket_handlers


def _patch_legacy_module_globals(app):
    import app as legacy_app

    legacy_app.app = app
    legacy_app.jwt = jwt
    legacy_app.limiter = limiter
    legacy_app.socketio = socketio


def create_app():
    app = Flask(__name__)
    app.config.from_object(ProductionConfig)
    app.config["ENV"] = os.getenv("FLASK_ENV", "production")
    app.config["DEBUG"] = False
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", str(10 * 1024 * 1024)))

    CORS(
        app,
        resources={r"/*": {"origins": ALLOWED_CORS_ORIGINS}},
        supports_credentials=True,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-CSRF-TOKEN"],
    )

    jwt.init_app(app)
    limiter.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins=ALLOWED_CORS_ORIGINS,
        async_mode=os.getenv("SOCKETIO_ASYNC_MODE", "threading"),
        logger=False,
        engineio_logger=False,
    )

    _patch_legacy_module_globals(app)

    register_monitoring_hooks(app)
    register_socket_handlers(socketio)

    app.register_blueprint(auth_bp)
    app.register_blueprint(gps_bp)
    app.register_blueprint(bookings_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(shipments_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(system_bp)

    return app
