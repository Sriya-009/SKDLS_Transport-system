from flask import Blueprint

from app import get_12_tyre_lorries as legacy_get_12_tyre_lorries
from app import get_14_tyre_lorries as legacy_get_14_tyre_lorries
from app import get_16_tyre_lorries as legacy_get_16_tyre_lorries
from app import gps_driver_heartbeat as legacy_gps_driver_heartbeat
from app import gps_eta as legacy_gps_eta
from app import gps_fleet as legacy_gps_fleet
from app import gps_fleet_heatmap as legacy_gps_fleet_heatmap
from app import gps_geofences as legacy_gps_geofences
from app import gps_ingest as legacy_gps_ingest
from app import gps_inactivity as legacy_gps_inactivity
from app import gps_logs as legacy_gps_logs
from app import gps_playback as legacy_gps_playback
from app import gps_route_optimization as legacy_gps_route_optimization
from app import gps_shipments_live as legacy_gps_shipments_live
from app import gps_truck as legacy_gps_truck
from app import track_lorry as legacy_track_lorry
from extensions import limiter
from middleware.auth import require_auth
from utils.blueprints import bind_route


gps_bp = Blueprint("gps_bp", __name__)

bind_route(gps_bp, "/lorries/12", legacy_get_12_tyre_lorries, "get_12_tyre_lorries", ["GET"])
bind_route(gps_bp, "/lorries/14", legacy_get_14_tyre_lorries, "get_14_tyre_lorries", ["GET"])
bind_route(gps_bp, "/lorries/16", legacy_get_16_tyre_lorries, "get_16_tyre_lorries", ["GET"])
bind_route(gps_bp, "/track/<lorry_number>", legacy_track_lorry, "track_lorry", ["GET"])
bind_route(gps_bp, "/gps/fleet", legacy_gps_fleet, "gps_fleet", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/trucks/<lorry_number>", legacy_gps_truck, "gps_truck", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/logs/<lorry_number>", legacy_gps_logs, "gps_logs", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/ingest", legacy_gps_ingest, "gps_ingest", ["POST"], [limiter.limit("60 per minute")])
bind_route(gps_bp, "/gps/playback/<int:booking_id>", legacy_gps_playback, "gps_playback", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/eta/<int:booking_id>", legacy_gps_eta, "gps_eta", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/shipments/live", legacy_gps_shipments_live, "gps_shipments_live", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/fleet/heatmap", legacy_gps_fleet_heatmap, "gps_fleet_heatmap", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/geofences", legacy_gps_geofences, "gps_geofences", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/inactivity", legacy_gps_inactivity, "gps_inactivity", ["GET"], [require_auth])
bind_route(gps_bp, "/gps/route-optimization", legacy_gps_route_optimization, "gps_route_optimization", ["POST"], [require_auth])
bind_route(gps_bp, "/gps/driver-heartbeat", legacy_gps_driver_heartbeat, "gps_driver_heartbeat", ["POST"], [limiter.limit("120 per minute")])
