from app import handle_tracking_connect as legacy_handle_tracking_connect
from app import handle_tracking_disconnect as legacy_handle_tracking_disconnect
from utils.blueprints import unwrap_view


def register_socket_handlers(socketio):
    socketio.on_event("connect", unwrap_view(legacy_handle_tracking_connect))
    socketio.on_event("disconnect", unwrap_view(legacy_handle_tracking_disconnect))
