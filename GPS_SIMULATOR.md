# GPS Simulator Documentation

## Overview

The GPS Simulator is a background thread that automatically updates lorry positions in the database every 10 seconds, simulating realistic movement along booking routes from source to destination.

## Features

- **Automatic Movement Simulation**: Updates lorry latitude/longitude every 10 seconds
- **Realistic Path Calculation**: Uses Haversine formula and bearing calculations for accurate paths
- **Active Booking Tracking**: Queries the bookings table to find routes in progress
- **Non-blocking**: Runs in a background thread without affecting Flask request handling
- **Graceful Shutdown**: Cleanly stops the simulator when Flask shuts down
- **Adaptive Tyre Type Handling**: Works with 12, 14, and 16 tyre lorry tables

## How It Works

### 1. Simulation Loop (Every 10 Seconds)
- Queries active bookings from the database
- For each active booking:
  - Gets the current lorry position
  - Calculates bearing and distance to destination
  - Moves the lorry 5 km toward destination
  - Updates database with new coordinates

### 2. Coordinate Calculation
- Uses Nominatim (OpenStreetMap) API to get city coordinates
- Calculates Haversine distance between points
- Uses bearing calculation for navigation direction
- Moves the point incrementally along the bearing line

### 3. Database Schema Integration
- Queries: `bookings` table for active routes
- Updates: `lorries_12_tyre`, `lorries_14_tyre`, `lorries_16_tyre` tables
- Column names supported:
  - Latitude: `latitude`, `current_latitude`, `lat`
  - Longitude: `longitude`, `current_longitude`, `lon`
  - Timestamp: `last_updated`

## Configuration

The simulator is automatically initialized when the Flask app starts:

```python
# backend/app.py
from gps_simulator import GPSSimulator

gps_simulator = GPSSimulator(DB_CONFIG, TABLE_MAP)
gps_simulator.start()

atexit.register(gps_simulator.stop)
```

## Example: Vijayawada to Hyderabad Route

For a lorry (e.g., AP16TE4055) on a booking from Vijayawada to Hyderabad:

1. **Initial Position**: Set to Vijayawada coordinates (17.36°N, 78.47°E)
2. **Each Step**: Moves ~5 km northeast toward Hyderabad
3. **Progress**: 
   - After 1 step: ~17.42°N, 78.50°E
   - After 2 steps: ~17.48°N, 78.53°E
   - After 3 steps: ~17.54°N, 78.56°E
   - ... continues until reaching Hyderabad (~17.36°N, 78.47°E)
4. **Arrival**: Stops at destination when distance < 5 km

## API Integration

### Tracking API
The live tracking API (`GET /track/<lorry_number>`) returns the GPS simulator-updated positions:

```json
{
  "status": "success",
  "lorry_number": "AP16TE4055",
  "latitude": 17.36,
  "longitude": 78.47,
  "last_updated": "2026-05-08T10:30:45.123456"
}
```

### Frontend Integration
The React tracking component receives updated positions every 10 seconds and displays:
- Live marker position on Leaflet map
- Smooth animation as lorry moves
- Auto-centering map on active position
- Last updated timestamp

## Performance Considerations

- **Network Calls**: Nominatim queries are cached per route (coordinates looked up once per booking)
- **Database Writes**: Only updates positions when movement occurs
- **Thread Safety**: Uses dedicated background thread, MySQL connections are thread-safe
- **Memory Usage**: Minimal (background thread only, no state storage beyond bookings)

## Error Handling

- **Coordinate Lookup Failures**: Silently skips if Nominatim can't find a location
- **Database Connection Failures**: Logs error and continues with next step
- **Invalid Column Names**: Adapter tries multiple column name variations
- **Missing Bookings**: Continues if no active bookings found

## Monitoring

Check simulator status:

```python
# In Flask app or test script
from app import gps_simulator

if gps_simulator.running:
    print("GPS Simulator is active")
```

## Stopping the Simulator

The simulator automatically stops when:
1. Flask app exits
2. `gps_simulator.stop()` is called manually
3. Python process terminates

## Customization

To modify simulator behavior, edit `backend/gps_simulator.py`:

- `update_interval`: Change from 10 seconds to desired interval
- `movement_distance`: Adjust 5000 meters per step to different distance
- Active booking query: Modify SQL WHERE clause to filter bookings differently
