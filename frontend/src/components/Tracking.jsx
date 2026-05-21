import { useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { API_BASE_URL } from '../services/apiBase'
import '../styles/Tracking.css'

// Fix Leaflet marker icon issue
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
})

const defaultCenter = [20.5937, 78.9629]
const defaultZoom = 5

const createTruckIcon = () =>
  L.divIcon({
    html: `
      <div style="
        background: linear-gradient(135deg, #0f62fe, #4c9aff);
        width: 44px;
        height: 44px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-weight: 700;
        border: 2px solid white;
        box-shadow: 0 6px 18px rgba(15, 98, 254, 0.35);
        font-size: 15px;
      ">🚛</div>
    `,
    className: 'custom-marker',
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -22],
  })

const truckIcon = createTruckIcon()

function MapFocus({ center, zoom }) {
  const map = useMap()

  useEffect(() => {
    map.flyTo(center, zoom, {
      animate: true,
      duration: 1.2,
      easeLinearity: 0.25,
    })
  }, [center, zoom, map])

  return null
}

function formatTimestamp(value) {
  if (!value) {
    return 'Not updated yet'
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return String(value)
  }

  return parsed.toLocaleString()
}

export default function Tracking() {
  const [lorryNumber, setLorryNumber] = useState('')
  const [trackingData, setTrackingData] = useState(null)
  const [markerPosition, setMarkerPosition] = useState(null)
  const [statusMessage, setStatusMessage] = useState('Enter a lorry number to start live tracking.')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const animationRef = useRef(null)

  const mapCenter = useMemo(() => {
    if (markerPosition?.latitude && markerPosition?.longitude) {
      return [markerPosition.latitude, markerPosition.longitude]
    }

    return defaultCenter
  }, [markerPosition])

  const animateMarkerPosition = (nextPosition) => {
    if (!nextPosition) {
      setMarkerPosition(null)
      return
    }

    if (animationRef.current) {
      window.cancelAnimationFrame(animationRef.current)
      animationRef.current = null
    }

    setMarkerPosition((previous) => {
      if (!previous) {
        return nextPosition
      }

      const startTime = performance.now()
      const durationMs = 1200

      const step = (now) => {
        const progress = Math.min((now - startTime) / durationMs, 1)
        const latitude = previous.latitude + (nextPosition.latitude - previous.latitude) * progress
        const longitude = previous.longitude + (nextPosition.longitude - previous.longitude) * progress

        setMarkerPosition({ latitude, longitude })

        if (progress < 1) {
          animationRef.current = window.requestAnimationFrame(step)
        } else {
          animationRef.current = null
        }
      }

      animationRef.current = window.requestAnimationFrame(step)
      return previous
    })
  }

  const getFriendlyTrackingError = (message) => {
    const normalized = String(message || '').toLowerCase()

    if (normalized.includes('not found')) {
      return 'Invalid lorry number. Please check and try again.'
    }

    if (normalized.includes('unavailable')) {
      return 'GPS data is currently unavailable for this lorry.'
    }

    return 'Unable to fetch tracking details right now. Please try again.'
  }

  const fetchTracking = async (targetLorryNumber) => {
    const trimmed = targetLorryNumber.trim()
    if (!trimmed) {
      setTrackingData(null)
      setStatusMessage('Enter a lorry number to start live tracking.')
      setError('')
      return
    }

    setLoading(true)
    setError('')

    try {
      const response = await fetch(`${API_BASE_URL}/track/${encodeURIComponent(trimmed)}`)
      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.message || 'Tracking data not available')
      }

      if (data.status === 'unavailable') {
        setTrackingData(null)
        setMarkerPosition(null)
        setStatusMessage(data.message || 'GPS data is unavailable for this lorry right now.')
        return
      }

      const nextTrackingData = {
        lorry_number: data.lorry_number,
        latitude: Number(data.latitude),
        longitude: Number(data.longitude),
        last_updated: data.last_updated || '',
      }

      setTrackingData(nextTrackingData)
      animateMarkerPosition({ latitude: nextTrackingData.latitude, longitude: nextTrackingData.longitude })
      setStatusMessage('Live location updated successfully.')
    } catch (fetchError) {
      setTrackingData(null)
      setMarkerPosition(null)
      const rawMessage = fetchError instanceof Error ? fetchError.message : 'Tracking data not available'
      setError(getFriendlyTrackingError(rawMessage))
      setStatusMessage('Unable to load GPS data right now.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!lorryNumber.trim()) {
      return undefined
    }

    fetchTracking(lorryNumber)
    const intervalId = window.setInterval(() => {
      fetchTracking(lorryNumber)
    }, 10000)

    return () => window.clearInterval(intervalId)
  }, [lorryNumber])

  useEffect(
    () => () => {
      if (animationRef.current) {
        window.cancelAnimationFrame(animationRef.current)
      }
    },
    [],
  )

  return (
    <div className="tracking-page">
      <h1>Vehicle Tracking</h1>
      <div className="tracking-container">
        <div className="map-section">
          <MapContainer center={mapCenter} zoom={trackingData ? 11 : defaultZoom} className="tracking-map">
            <MapFocus center={mapCenter} zoom={trackingData ? 11 : defaultZoom} />
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            />

            {trackingData && markerPosition && (
              <Marker position={[markerPosition.latitude, markerPosition.longitude]} icon={truckIcon}>
                <Popup>
                  <div className="vehicle-popup">
                    <h3>{trackingData.lorry_number}</h3>
                    <p>
                      <strong>Status:</strong> Live GPS
                    </p>
                    <p>
                      <strong>Lat:</strong> {trackingData.latitude.toFixed(4)}
                    </p>
                    <p>
                      <strong>Lon:</strong> {trackingData.longitude.toFixed(4)}
                    </p>
                    <p>
                      <strong>Updated:</strong> {formatTimestamp(trackingData.last_updated)}
                    </p>
                  </div>
                </Popup>
              </Marker>
            )}
          </MapContainer>
          {loading && (
            <div className="tracking-map-loading">
              <span>Fetching latest GPS...</span>
            </div>
          )}
        </div>

        <div className="vehicles-list">
          <h2>Live Lorry Tracking</h2>
          <div className="tracking-search">
            <label htmlFor="lorryNumber">Lorry Number</label>
            <input
              id="lorryNumber"
              type="text"
              value={lorryNumber}
              onChange={(event) => setLorryNumber(event.target.value)}
              placeholder="Enter lorry number"
            />
            <button type="button" onClick={() => fetchTracking(lorryNumber)} disabled={loading}>
              {loading ? 'Loading...' : 'Track Lorry'}
            </button>
          </div>

          <div className="tracking-status">
            <p>{statusMessage}</p>
            {error && <p className="tracking-error">{error}</p>}
          </div>

          <div className="vehicles-grid">
            <div className={`vehicle-card ${trackingData ? 'active' : ''}`}>
              <div className="vehicle-header">
                <h3>{trackingData?.lorry_number || 'No lorry selected'}</h3>
                <span className={`status-badge ${trackingData ? 'in-transit' : 'idle'}`}>
                  {trackingData ? 'Live' : 'Idle'}
                </span>
              </div>
              <div className="vehicle-details">
                <p>
                  <strong>Latitude:</strong>{' '}
                  {trackingData ? trackingData.latitude.toFixed(4) : '—'}
                </p>
                <p>
                  <strong>Longitude:</strong>{' '}
                  {trackingData ? trackingData.longitude.toFixed(4) : '—'}
                </p>
                <p>
                  <strong>Last Updated:</strong>{' '}
                  {trackingData ? formatTimestamp(trackingData.last_updated) : '—'}
                </p>
                <p>
                  <strong>Refresh:</strong> Every 10 seconds
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
