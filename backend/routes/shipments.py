from flask import Blueprint
from flask_jwt_extended import jwt_required

from app import create_shipment as legacy_create_shipment
from app import get_shipment as legacy_get_shipment
from app import my_shipments as legacy_my_shipments
from app import shipment_activity_feed as legacy_shipment_activity_feed
from app import shipment_events as legacy_shipment_events
from app import shipment_invoice as legacy_shipment_invoice
from app import shipment_timeline as legacy_shipment_timeline
from utils.blueprints import bind_route


shipments_bp = Blueprint("shipments_bp", __name__)

bind_route(shipments_bp, "/api/shipments", legacy_create_shipment, "api_create_shipment", ["POST"], [jwt_required(optional=True)])
bind_route(shipments_bp, "/shipments", legacy_create_shipment, "create_shipment", ["POST"], [jwt_required(optional=True)])
bind_route(shipments_bp, "/api/shipments/my", legacy_my_shipments, "my_shipments", ["GET"], [jwt_required()])
bind_route(shipments_bp, "/api/shipments/<int:shipment_id>", legacy_get_shipment, "get_shipment", ["GET"], [jwt_required()])
bind_route(shipments_bp, "/api/shipments/<int:shipment_id>/timeline", legacy_shipment_timeline, "shipment_timeline", ["GET"], [jwt_required()])
bind_route(shipments_bp, "/api/shipments/<int:shipment_id>/events", legacy_shipment_events, "shipment_events", ["GET"], [jwt_required()])
bind_route(shipments_bp, "/api/shipments/activity-feed", legacy_shipment_activity_feed, "shipment_activity_feed", ["GET"], [jwt_required(optional=True)])
bind_route(shipments_bp, "/api/shipments/<int:shipment_id>/invoice", legacy_shipment_invoice, "shipment_invoice", ["GET"], [jwt_required()])
