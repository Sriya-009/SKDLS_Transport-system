import threading
import time
import math
import random
import requests
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from logging_utils import logger, setup_logger, log_print

logger = setup_logger()
print = log_print

class GPSSimulator:
    """Background GPS simulator for realistic lorry movement."""
    
    def __init__(self, db_config, table_map, on_update_callback=None):
        """
        Initialize GPS simulator.
        
        Args:
            db_config: MySQL connection configuration dict
            table_map: Dict mapping tyre types to table names (e.g., {"12": "lorries_12_tyre"})
        """
        self.db_config = db_config
        self.table_map = table_map
        self.running = False
        self.thread = None
        self.update_interval = 10  # seconds
        self.on_update_callback = on_update_callback
        self.last_updates = []
        # traffic simulation parameters
        self.traffic_chance = 0.2
        self.min_traffic_slowdown = 0.3
        self.max_traffic_slowdown = 0.8

    def set_update_callback(self, callback):
        self.on_update_callback = callback

    def _get_table_columns(self, table_name):
        connection = None
        cursor = None

        try:
            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor()
            cursor.execute(f"SHOW COLUMNS FROM {table_name}")
            return {row[0] for row in cursor.fetchall()}
        except Error as e:
            print(f"[GPS Simulator] Error reading schema for {table_name}: {e}")
            return set()
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def _log_missing_columns(self, table_name, required_columns, actual_columns):
        missing_columns = sorted(set(required_columns) - set(actual_columns))
        if missing_columns:
            print(f"[GPS Simulator][schema][warning] {table_name} missing columns: {', '.join(missing_columns)}")
        return missing_columns
        
    def start(self):
        """Start the GPS simulator in a background thread."""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_simulation_loop, daemon=True)
        self.thread.start()
        print("[GPS Simulator] Started successfully")
    
    def stop(self):
        """Stop the GPS simulator gracefully."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[GPS Simulator] Stopped")
    
    def _run_simulation_loop(self):
        """Main simulation loop running in background thread."""
        while self.running:
            try:
                self._simulate_step()
            except Exception as e:
                print(f"[GPS Simulator] Error in simulation step: {e}")
            finally:
                self._notify_update()
            
            # Sleep in small increments to allow graceful shutdown
            for _ in range(self.update_interval):
                if not self.running:
                    break
                time.sleep(1)
    
    def _simulate_step(self):
        """Execute one simulation step: update all active lorry positions."""
        active_bookings = self._get_active_bookings()
        
        for booking in active_bookings:
            try:
                self._update_lorry_position(booking)
            except Exception as e:
                print(f"[GPS Simulator] Error updating lorry {booking.get('lorry_number')}: {e}")

    def _notify_update(self):
        if not callable(self.on_update_callback):
            # clear last updates regardless
            self.last_updates = []
            return

        try:
            # pass a copy of last updates for consumers
            updates = list(self.last_updates)
            # clear stored updates for next cycle
            self.last_updates = []
            self.on_update_callback(updates)
        except Exception as e:
            print(f"[GPS Simulator] Error broadcasting live update: {e}")
    
    def _get_active_bookings(self):
        """Query all active bookings from bookings table."""
        connection = None
        cursor = None
        required_columns = {
            "id",
            "lorry_number",
            "source_location",
            "destination_location",
            "tyre_type",
            "booking_status",
            "created_at",
        }
        
        try:
            actual_columns = self._get_table_columns("bookings")
            missing_columns = self._log_missing_columns("bookings", required_columns, actual_columns)
            if "lorry_number" not in actual_columns or "source_location" not in actual_columns or "destination_location" not in actual_columns or "tyre_type" not in actual_columns:
                return []

            select_columns = [
                column_name
                for column_name in ["id", "lorry_number", "source_location", "destination_location", "tyre_type", "booking_status", "created_at"]
                if column_name in actual_columns
            ]

            if not select_columns:
                return []

            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor(dictionary=True)
            
            select_sql = ", ".join(select_columns)
            if "booking_status" in actual_columns:
                query = f"""
                    SELECT {select_sql}
                    FROM bookings
                    WHERE booking_status IS NULL
                       OR booking_status NOT IN ('completed', 'cancelled')
                    LIMIT 50
                """
            else:
                query = f"SELECT {select_sql} FROM bookings LIMIT 50"

            cursor.execute(query)
            
            return cursor.fetchall()
        except Error as e:
            print(f"[GPS Simulator] Database error fetching bookings: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def _get_coordinates(self, location):
        """Get latitude/longitude for a city using Nominatim."""
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": location, "format": "json", "limit": 1},
                headers={"User-Agent": "SKDLS Transportations"},
                timeout=10,
            )
            
            if not response.ok or not response.json():
                return None, None
            
            data = response.json()[0]
            lat = float(data["lat"])
            lon = float(data["lon"])
            
            return lat, lon
        except Exception as e:
            print(f"[GPS Simulator] Error getting coordinates for '{location}': {e}")
            return None, None
    
    def _get_distance_meters(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two coordinates using Haversine formula (in meters)."""
        R = 6371000  # Earth radius in meters
        
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def _calculate_bearing(self, lat1, lon1, lat2, lon2):
        """Calculate bearing (direction) from point 1 to point 2."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_lambda = math.radians(lon2 - lon1)
        
        x = math.sin(delta_lambda) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
        
        bearing_rad = math.atan2(x, y)
        bearing_deg = math.degrees(bearing_rad)
        
        return (bearing_deg + 360) % 360
    
    def _move_point(self, lat, lon, bearing, distance_meters):
        """Move a point by given distance in given direction."""
        R = 6371000  # Earth radius in meters
        
        phi1 = math.radians(lat)
        lambda1 = math.radians(lon)
        bearing_rad = math.radians(bearing)
        
        phi2 = math.asin(
            math.sin(phi1) * math.cos(distance_meters / R)
            + math.cos(phi1) * math.sin(distance_meters / R) * math.cos(bearing_rad)
        )
        
        lambda2 = lambda1 + math.atan2(
            math.sin(bearing_rad) * math.sin(distance_meters / R) * math.cos(phi1),
            math.cos(distance_meters / R) - math.sin(phi1) * math.sin(phi2),
        )
        
        return math.degrees(phi2), math.degrees(lambda2)
    
    def _get_lorry_current_position(self, table_name, lorry_number):
        """Get current position of a lorry from the database."""
        connection = None
        cursor = None
        
        try:
            actual_columns = self._get_table_columns(table_name)
            required_columns = {"vehicle_number", "latitude", "longitude"}
            missing_columns = self._log_missing_columns(table_name, required_columns, actual_columns)
            if missing_columns:
                return None, None

            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute(
                f"SELECT vehicle_number, latitude, longitude FROM {table_name} WHERE vehicle_number = %s LIMIT 1",
                (lorry_number,)
            )
            
            row = cursor.fetchone()
            if not row:
                return None, None
            
            lat = row.get("latitude")
            lon = row.get("longitude")
            
            if lat is None or lon is None:
                return None, None
            
            return float(lat), float(lon)
        except Error as e:
            print(f"[GPS Simulator] Error fetching lorry position: {e}")
            return None, None
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
    
    def _update_lorry_position(self, booking):
        """Update a lorry's position toward its destination."""
        booking_id = booking.get("id")
        lorry_number = booking.get("lorry_number")
        source = booking.get("source_location")
        destination = booking.get("destination_location")
        tyre_type = booking.get("tyre_type", "12")
        
        if not all([lorry_number, source, destination]):
            return
        
        # Normalize tyre type to table key (convert int to string if needed)
        tyre_key = str(tyre_type).strip()
        if tyre_key not in self.table_map:
            return
        
        table_name = self.table_map[tyre_key]
        
        # Get current position
        current_lat, current_lon = self._get_lorry_current_position(table_name, lorry_number)
        if current_lat is None:
            # First time - initialize to source location
            current_lat, current_lon = self._get_coordinates(source)
            if current_lat is None:
                return
        
        # Get destination coordinates
        dest_lat, dest_lon = self._get_coordinates(destination)
        if dest_lat is None:
            return
        
        # Calculate distance to destination and bearing
        distance_to_dest = self._get_distance_meters(current_lat, current_lon, dest_lat, dest_lon)
        bearing = self._calculate_bearing(current_lat, current_lon, dest_lat, dest_lon)
        
        # Base movement per step (meters)
        base_movement_distance = 5000

        # Traffic simulation: random slowdown factor
        slowdown_factor = 1.0
        try:
            if random.random() < self.traffic_chance:
                slowdown_factor = float(random.uniform(self.min_traffic_slowdown, self.max_traffic_slowdown))
        except Exception:
            slowdown_factor = 1.0

        movement_distance = int(base_movement_distance * slowdown_factor)
        
        # If closer than movement distance, snap to destination
        if distance_to_dest <= movement_distance:
            new_lat, new_lon = dest_lat, dest_lon
        else:
            new_lat, new_lon = self._move_point(current_lat, current_lon, bearing, movement_distance)
        
        # Update database
        self._save_lorry_position(table_name, lorry_number, new_lat, new_lon)
        self._save_gps_log(booking_id, lorry_number, new_lat, new_lon, source, destination)

        # Record last update for on_update_callback consumers
        try:
            self.last_updates.append({
                "booking_id": int(booking_id) if booking_id is not None else None,
                "lorry_number": str(lorry_number),
                "latitude": float(new_lat),
                "longitude": float(new_lon),
                "source_location": str(source or ""),
                "destination_location": str(destination or ""),
                "timestamp": datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
                "traffic_slowdown": round(1.0 - slowdown_factor, 2) if slowdown_factor < 1.0 else 0.0,
            })
        except Exception:
            pass
    
    def _save_lorry_position(self, table_name, lorry_number, latitude, longitude):
        """Save updated position to database."""
        connection = None
        cursor = None
        
        try:
            actual_columns = self._get_table_columns(table_name)
            required_columns = {"vehicle_number", "latitude", "longitude", "last_updated"}
            missing_columns = self._log_missing_columns(table_name, required_columns, actual_columns)
            if missing_columns:
                return

            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor()
            
            # Update the position
            update_query = f"""
                UPDATE {table_name}
                SET latitude = %s, longitude = %s, last_updated = %s
                WHERE vehicle_number = %s
            """
            
            cursor.execute(update_query, (latitude, longitude, datetime.now().isoformat(), lorry_number))
            connection.commit()
        except Error as e:
            print(f"[GPS Simulator] Error saving lorry position: {e}")
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

    def _save_gps_log(self, booking_id, lorry_number, latitude, longitude, source_location, destination_location):
        """Persist each GPS update for tracking history and dashboard playback."""
        connection = None
        cursor = None

        try:
            connection = mysql.connector.connect(**self.db_config)
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO gps_logs
                    (booking_id, lorry_number, latitude, longitude, source_location, destination_location, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(booking_id) if booking_id is not None else None,
                    str(lorry_number),
                    float(latitude),
                    float(longitude),
                    str(source_location or "").strip() or None,
                    str(destination_location or "").strip() or None,
                    datetime.now().isoformat(),
                ),
            )
            connection.commit()
        except Error as e:
            print(f"[GPS Simulator] Error saving GPS log: {e}")
        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()
