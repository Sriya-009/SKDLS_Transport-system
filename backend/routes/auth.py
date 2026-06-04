from flask import Blueprint
from flask_jwt_extended import jwt_required

from app import auth_login as legacy_auth_login
from app import auth_logout as legacy_auth_logout
from app import auth_profile as legacy_auth_profile
from app import auth_refresh as legacy_auth_refresh
from app import auth_register as legacy_auth_register
from app import driver_login as legacy_driver_login
from utils.blueprints import bind_route


auth_bp = Blueprint("auth_bp", __name__)

bind_route(auth_bp, "/api/auth/register", legacy_auth_register, "api_auth_register", ["POST"])
bind_route(auth_bp, "/auth/register", legacy_auth_register, "auth_register", ["POST"])
bind_route(auth_bp, "/auth/driver/login", legacy_driver_login, "driver_login", ["POST"])
bind_route(auth_bp, "/api/auth/login", legacy_auth_login, "api_auth_login", ["POST"])
bind_route(auth_bp, "/auth/login", legacy_auth_login, "auth_login", ["POST"])
bind_route(auth_bp, "/api/auth/profile", legacy_auth_profile, "api_auth_profile", ["GET"], [jwt_required()])
bind_route(auth_bp, "/auth/profile", legacy_auth_profile, "auth_profile", ["GET"], [jwt_required()])
bind_route(auth_bp, "/api/auth/refresh", legacy_auth_refresh, "api_auth_refresh", ["POST"], [jwt_required(refresh=True)])
bind_route(auth_bp, "/auth/refresh", legacy_auth_refresh, "auth_refresh", ["POST"], [jwt_required(refresh=True)])
bind_route(auth_bp, "/api/auth/logout", legacy_auth_logout, "api_auth_logout", ["POST"], [jwt_required()])
bind_route(auth_bp, "/auth/logout", legacy_auth_logout, "auth_logout", ["POST"], [jwt_required()])
