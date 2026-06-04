import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, CircleMarker, MapContainer, Marker, Popup, Polyline, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { io } from 'socket.io-client'
import { requestJson } from '../services/http'
import { SOCKET_BASE_URL } from '../services/apiBase'
import { EmptyState } from './AppChrome'
import '../styles/Tracking.css'

L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
})

const defaultCenter = [20.5937, 78.9629]
const defaultZoom = 5

function createTruckIcon({ selected = false, label = '🚛' } = {}) {
  const accent = selected ? '#f97316' : '#4c9aff'

  return L.divIcon({
    html: `
      <div style="
        background: linear-gradient(135deg, ${accent}, ${selected ? '#fb923c' : '#1d4ed8'});
        width: ${selected ? '48px' : '42px'};
        height: ${selected ? '48px' : '42px'};
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-weight: 800;
        border: 2px solid rgba(255,255,255,0.9);
        box-shadow: 0 12px 26px rgba(15, 23, 42, 0.35);
        font-size: ${selected ? '18px' : '15px'};
      ">${label}</div>
    `,
    className: 'tracking-marker',
    iconSize: [selected ? 48 : 42, selected ? 48 : 42],
    iconAnchor: [selected ? 24 : 21, selected ? 24 : 21],
    popupAnchor: [0, selected ? -24 : -21],
  })
}

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

function formatEta(etaHours) {
  if (etaHours === null || etaHours === undefined || Number.isNaN(Number(etaHours))) {
    return 'Pending'
  }

  const numeric = Number(etaHours)
  if (numeric < 1) {
    const minutes = Math.max(1, Math.round(numeric * 60))
    return `${minutes} min`
  }

  return `${numeric.toFixed(1).replace(/\.0$/, '')} hrs`
}

function formatDistance(distanceKm) {
  if (distanceKm === null || distanceKm === undefined || Number.isNaN(Number(distanceKm))) {
    return 'Pending'
  }

  return `${Number(distanceKm).toFixed(1).replace(/\.0$/, '')} km`
}

function haversineKm(lat1, lng1, lat2, lng2) {
  if (![lat1, lng1, lat2, lng2].every((value) => Number.isFinite(Number(value)))) {
    return Infinity
  }

  const toRad = (value) => (Number(value) * Math.PI) / 180
  const earthRadius = 6371
  const dLat = toRad(lat2 - lat1)
  const dLng = toRad(lng2 - lng1)
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) * Math.sin(dLng / 2)
  return 2 * earthRadius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function formatStopDuration(minutes) {
  const value = Number(minutes)
  if (!Number.isFinite(value) || value <= 0) return 'Moving'
  if (value < 60) return `${Math.round(value)} min stop`
  return `${Math.round(value / 60)} hr stop`
}

function parsePoint(point) {
  if (!point) return null
  if (Array.isArray(point) && point.length >= 2) {
    const lat = Number(point[0])
    const lng = Number(point[1])
    return Number.isFinite(lat) && Number.isFinite(lng) ? { lat, lng } : null
  }
  if (typeof point === 'object') {
    const lat = Number(point.latitude ?? point.lat)
    const lng = Number(point.longitude ?? point.lng)
    return Number.isFinite(lat) && Number.isFinite(lng) ? { lat, lng } : null
  }
  return null
}

function flattenRoutePoints(trucks = []) {
  return trucks.flatMap((truck) => (Array.isArray(truck.route_coordinates) ? truck.route_coordinates : []).map(parsePoint).filter(Boolean))
}

export default function Tracking() {
  const [fleet, setFleet] = useState([])
  const [selectedLorryNumber, setSelectedLorryNumber] = useState('')
  const [historyLogs, setHistoryLogs] = useState([])
  const [searchInput, setSearchInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState('')
  const [statusMessage, setStatusMessage] = useState('Loading live truck positions...')
  const [showTrafficOverlay, setShowTrafficOverlay] = useState(true)
  const [showHeatmap, setShowHeatmap] = useState(true)
  const [showGeofences, setShowGeofences] = useState(true)
  const [playbackLogs, setPlaybackLogs] = useState([])
  const [playbackIndex, setPlaybackIndex] = useState(0)
  const [playbackMode, setPlaybackMode] = useState(false)
  const [trackingAlerts, setTrackingAlerts] = useState([])
  const animationFrameRef = useRef(null)
  const socketRef = useRef(null)
  const fleetRef = useRef([])
  const playbackTimerRef = useRef(null)
  const playbackRequestRef = useRef(0)

  useEffect(() => {
    fleetRef.current = fleet
  }, [fleet])

  const visibleFleet = fleet

  const selectedTruck = useMemo(() => {
    if (!visibleFleet.length) {
      return null
    }

    if (selectedLorryNumber) {
      const exact = visibleFleet.find((truck) => truck.lorry_number === selectedLorryNumber)
      if (exact) {
        return exact
      }
    }

    return visibleFleet[0]
  }, [visibleFleet, selectedLorryNumber])

  const mapCenter = useMemo(() => {
    if (selectedTruck?.latitude && selectedTruck?.longitude) {
      return [selectedTruck.latitude, selectedTruck.longitude]
    }

    if (visibleFleet.length > 0) {
      const firstLiveTruck = visibleFleet.find((truck) => truck.latitude && truck.longitude)
      if (firstLiveTruck) {
        return [firstLiveTruck.latitude, firstLiveTruck.longitude]
      }
    }

    return defaultCenter
  }, [selectedTruck, visibleFleet])

  const selectedTrail = useMemo(() => {
    const sourceLogs = playbackMode && playbackLogs.length ? playbackLogs.slice(0, Math.max(1, playbackIndex + 1)) : historyLogs

    if (!sourceLogs.length) {
      return []
    }

    return sourceLogs
      .slice()
      .reverse()
      .map((entry) => [entry.latitude, entry.longitude])
      .filter(([latitude, longitude]) => Number.isFinite(latitude) && Number.isFinite(longitude))
  }, [historyLogs, playbackLogs, playbackIndex, playbackMode])

  const selectedRoute = useMemo(() => {
    const route = selectedTruck?.route_coordinates || []
    return route
      .map(parsePoint)
      .filter(Boolean)
      .map((point) => [point.lat, point.lng])
  }, [selectedTruck])

  const heatmapPoints = useMemo(() => {
    const points = flattenRoutePoints(visibleFleet)
    const latestFleetPoints = visibleFleet
      .map((truck) => parsePoint([truck.latitude, truck.longitude]))
      .filter(Boolean)
    const trailPoints = selectedTrail.map(([lat, lng]) => parsePoint([lat, lng])).filter(Boolean)

    return [...points, ...latestFleetPoints, ...trailPoints]
  }, [visibleFleet, selectedTrail])

  const geofenceZones = useMemo(() => {
    const zones = []
    const routePoints = selectedRoute.length > 1 ? selectedRoute : selectedTrail
    if (routePoints.length > 1) {
      zones.push({ id: 'pickup', center: routePoints[0], radius: 500, label: 'Pickup geofence' })
      zones.push({ id: 'drop', center: routePoints[routePoints.length - 1], radius: 500, label: 'Drop geofence' })
    }
    if (selectedTruck?.latitude && selectedTruck?.longitude) {
      zones.push({ id: 'current', center: [selectedTruck.latitude, selectedTruck.longitude], radius: 300, label: 'Current position' })
    }
    return zones
  }, [selectedRoute, selectedTrail, selectedTruck])

  const stopDetection = useMemo(() => {
    const logs = (playbackMode && playbackLogs.length ? playbackLogs : historyLogs).slice(-5)
    if (logs.length < 3) return null
    const recent = logs.map((entry) => ({
      lat: Number(entry.latitude),
      lng: Number(entry.longitude),
      ts: new Date(entry.created_at || entry.timestamp || Date.now()).getTime(),
    }))
    if (!recent.every((entry) => Number.isFinite(entry.lat) && Number.isFinite(entry.lng))) return null
    const spreadKm = Math.max(...recent.map((entry) => haversineKm(recent[0].lat, recent[0].lng, entry.lat, entry.lng)))
    const timeSpanMin = (Math.max(...recent.map((entry) => entry.ts)) - Math.min(...recent.map((entry) => entry.ts))) / 60000
    if (spreadKm <= 0.25 && timeSpanMin >= 8) {
      return {
        truck: selectedTruck?.lorry_number || '',
        message: `${selectedTruck?.lorry_number || 'Truck'} appears stopped for ${formatStopDuration(timeSpanMin)} near the same location.`,
      }
    }
    return null
  }, [historyLogs, playbackLogs, playbackMode, selectedTruck])

  useEffect(() => {
    if (!stopDetection) return
    setTrackingAlerts((prev) => {
      if (prev.some((alert) => alert.message === stopDetection.message)) return prev
      return [{ id: `stop-${Date.now()}`, type: 'stop', message: stopDetection.message }, ...prev].slice(0, 8)
    })
  }, [stopDetection])

  const animateFleetUpdate = (nextFleet) => {
    if (animationFrameRef.current) {
      window.cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }

    const previousFleet = fleetRef.current
    if (!previousFleet.length) {
      setFleet(nextFleet)
      return
    }

    const previousByKey = new Map(previousFleet.map((truck) => [truck.lorry_number, truck]))
    const nextByKey = new Map(nextFleet.map((truck) => [truck.lorry_number, truck]))
    const startTime = performance.now()
    const durationMs = 1500

    const step = (now) => {
      const progress = Math.min((now - startTime) / durationMs, 1)

      const interpolated = nextFleet.map((nextTruck) => {
        const previousTruck = previousByKey.get(nextTruck.lorry_number)
        if (!previousTruck || !Number.isFinite(previousTruck.latitude) || !Number.isFinite(previousTruck.longitude)) {
          return nextTruck
        }

        if (!Number.isFinite(nextTruck.latitude) || !Number.isFinite(nextTruck.longitude)) {
          return nextTruck
        }

        return {
          ...nextTruck,
          latitude: previousTruck.latitude + (nextTruck.latitude - previousTruck.latitude) * progress,
          longitude: previousTruck.longitude + (nextTruck.longitude - previousTruck.longitude) * progress,
        }
      })

      setFleet(interpolated)

      if (progress < 1) {
        animationFrameRef.current = window.requestAnimationFrame(step)
      } else {
        animationFrameRef.current = null
        setFleet(nextFleet)
      }
    }

    animationFrameRef.current = window.requestAnimationFrame(step)
  }

  const applyFleetPayload = (payload) => {
    const trucks = Array.isArray(payload?.trucks) ? payload.trucks.map(normalizeTruck) : []
    animateFleetUpdate(trucks)
    setLoading(false)

    if (payload?.status === 'success' && trucks.length > 0) {
      setStatusMessage(`Live socket feed active. ${trucks.length} trucks tracked in real time.`)
    }

    if (trucks.length > 0 && (!selectedLorryNumber || !trucks.some((truck) => truck.lorry_number === selectedLorryNumber))) {
      setSelectedLorryNumber(trucks[0].lorry_number)
    }

    if (selectedLorryNumber) {
      const trackedTruck = trucks.find((truck) => truck.lorry_number === selectedLorryNumber)
      if (trackedTruck && Number.isFinite(trackedTruck.latitude) && Number.isFinite(trackedTruck.longitude)) {
        const alertTruck = trackedTruck.lorry_number
        setTrackingAlerts((prev) => {
          const duplicate = prev.find((alert) => alert.truck === alertTruck && alert.type === 'geofence')
          const routePoints = Array.isArray(trackedTruck.route_coordinates) ? trackedTruck.route_coordinates.map(parsePoint).filter(Boolean) : []
          if (routePoints.length > 1) {
            const start = routePoints[0]
            const end = routePoints[routePoints.length - 1]
            const nearStart = haversineKm(trackedTruck.latitude, trackedTruck.longitude, start.lat, start.lng) <= 0.45
            const nearEnd = haversineKm(trackedTruck.latitude, trackedTruck.longitude, end.lat, end.lng) <= 0.45
            if ((nearStart || nearEnd) && !duplicate) {
              return [{ id: `geo-${Date.now()}`, truck: alertTruck, type: 'geofence', message: `${alertTruck} entered a geofence zone.` }, ...prev].slice(0, 8)
            }
          }
          return prev
        })
      }
    }
  }

  const normalizeTruck = (truck) => ({
    booking_id: truck.booking_id ?? null,
    shipment_id: truck.shipment_id ?? truck.shipment?.id ?? null,
    driver_id: truck.driver_id ?? truck.driver?.id ?? null,
    driver: truck.driver ?? null,
    lorry_number: String(truck.lorry_number ?? '').trim(),
    truck_type: String(truck.truck_type ?? '').trim(),
    booking_status: String(truck.booking_status ?? '').trim(),
    pickup_location: String(truck.pickup_location ?? '').trim(),
    drop_location: String(truck.drop_location ?? '').trim(),
    latitude: Number(truck.latitude),
    longitude: Number(truck.longitude),
    last_updated: String(truck.last_updated ?? '').trim(),
    speed_kmph: truck.speed_kmph ?? null,
    heading_degrees: truck.heading_degrees ?? null,
    distance_km: truck.distance_km ?? null,
    eta_hours: truck.eta_hours ?? null,
    route_coordinates: Array.isArray(truck.route_coordinates) ? truck.route_coordinates : [],
    optimized_route: Array.isArray(truck.optimized_route) ? truck.optimized_route : [],
    route_progress: truck.route_progress ?? null,
    geofences: Array.isArray(truck.geofences) ? truck.geofences : [],
    heartbeat: truck.heartbeat ?? null,
    traffic: truck.traffic ?? null,
    alerts: Array.isArray(truck.alerts) ? truck.alerts : [],
    timeline: Array.isArray(truck.timeline) ? truck.timeline : [],
    gps_available: Boolean(truck.gps_available),
    shipment_status: String(truck.shipment_status ?? truck.status ?? truck.booking_status ?? '').trim(),
  })

  const fetchFleet = async () => {
    setLoading(true)
    setError('')

    try {
      const data = await requestJson('/gps/fleet')

      const trucks = Array.isArray(data.trucks) ? data.trucks.map(normalizeTruck) : []
      animateFleetUpdate(trucks)
      setStatusMessage(trucks.length > 0 ? 'Fleet live and updating every few seconds.' : 'No active trucks available right now.')

      if (trucks.length > 0 && (!selectedLorryNumber || !trucks.some((truck) => truck.lorry_number === selectedLorryNumber))) {
        setSelectedLorryNumber(trucks[0].lorry_number)
      }
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'Unable to load fleet tracking data')
      setStatusMessage('Tracking dashboard is temporarily unavailable.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const socket = io(SOCKET_BASE_URL, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      withCredentials: true,
      reconnection: true,
      reconnectionDelayMax: 5000,
    })

    socketRef.current = socket

    socket.on('connect', () => {
      setError('')
      setStatusMessage('Socket connected. Streaming fleet positions live.')
    })

    socket.on('fleet:update', (payload) => {
      applyFleetPayload(payload)
    })

    socket.on('truck:location', (payload) => {
      try {
        const lorry = String(payload?.lorry_number || '').trim()
        if (!lorry) return

        const updated = normalizeTruck({
          booking_id: payload.booking_id ?? null,
          lorry_number: lorry,
          latitude: payload.latitude,
          longitude: payload.longitude,
          last_updated: payload.last_updated || payload.timestamp || new Date().toISOString(),
          speed_kmph: payload.speed_kmph,
          heading_degrees: payload.heading_degrees,
          heartbeat: payload.heartbeat,
          traffic: payload.traffic,
          geofences: payload.geofences,
          route_progress: payload.route_progress,
          alerts: payload.alerts,
          gps_available: true,
        })

        // Merge into fleet
        const next = (fleetRef.current || []).slice()
        const idx = next.findIndex((t) => t.lorry_number === lorry)
        if (idx >= 0) {
          next[idx] = { ...next[idx], ...updated }
        } else {
          next.unshift(updated)
        }
        animateFleetUpdate(next)

        const prevTruck = fleetRef.current.find((item) => item.lorry_number === lorry)
        const moved = prevTruck
          ? haversineKm(prevTruck.latitude, prevTruck.longitude, updated.latitude, updated.longitude)
          : Infinity
        if (Number.isFinite(moved) && moved < 0.05) {
          setTrackingAlerts((prev) => {
            const message = `${lorry} is moving slowly or paused at ${updated.latitude.toFixed(4)}, ${updated.longitude.toFixed(4)}.`
            if (prev.some((alert) => alert.message === message)) return prev
            return [{ id: `move-${Date.now()}`, type: 'traffic', message }, ...prev].slice(0, 8)
          })
        }
      } catch (e) {
        // ignore
      }
    })

    socket.on('driver:heartbeat', (payload) => {
      const lorry = String(payload?.lorry_number || '').trim()
      if (!lorry) return
      const next = (fleetRef.current || []).slice()
      const idx = next.findIndex((t) => t.lorry_number === lorry)
      if (idx >= 0) {
        next[idx] = {
          ...next[idx],
          heartbeat: payload.heartbeat ?? next[idx].heartbeat,
          speed_kmph: payload.speed_kmph ?? next[idx].speed_kmph,
          latitude: Number.isFinite(Number(payload.latitude)) ? Number(payload.latitude) : next[idx].latitude,
          longitude: Number.isFinite(Number(payload.longitude)) ? Number(payload.longitude) : next[idx].longitude,
          last_updated: payload.timestamp || next[idx].last_updated,
        }
        animateFleetUpdate(next)
      }
    })

    socket.on('eta:update', (payload) => {
      const truckPayload = payload?.truck || payload?.snapshot || payload
      const lorry = String(truckPayload?.lorry_number || '').trim()
      if (!lorry) return
      const updated = normalizeTruck(truckPayload)
      const next = (fleetRef.current || []).slice()
      const idx = next.findIndex((t) => t.lorry_number === lorry)
      if (idx >= 0) next[idx] = { ...next[idx], ...updated }
      else next.unshift(updated)
      animateFleetUpdate(next)
    })

    socket.on('tracking:update', (payload) => {
      const truckPayload = payload?.truck || payload?.snapshot || payload
      if (truckPayload?.lorry_number) {
        const updated = normalizeTruck(truckPayload)
        const next = (fleetRef.current || []).slice()
        const idx = next.findIndex((t) => t.lorry_number === updated.lorry_number)
        if (idx >= 0) next[idx] = { ...next[idx], ...updated }
        else next.unshift(updated)
        animateFleetUpdate(next)
      }
    })

    socket.on('connect_error', () => {
      setStatusMessage('Socket connection failed. Falling back to live polling.')
    })

    socket.on('disconnect', () => {
      setStatusMessage('Socket disconnected. Polling remains active.')
    })

    return () => {
      socket.off('fleet:update')
      socket.off('truck:location')
      socket.off('driver:heartbeat')
      socket.off('eta:update')
      socket.off('tracking:update')
      socket.disconnect()
      socketRef.current = null
    }
  }, [])

  // Playback controls
  const [playing, setPlaying] = useState(false)
  const [playbackSpeed, setPlaybackSpeed] = useState(1)
  const playbackIndexRef = useRef(0)

  const fetchPlaybackLogs = async (bookingId) => {
    if (!bookingId) return []
    try {
      const res = await requestJson(`/gps/playback/${encodeURIComponent(bookingId)}?limit=1000`)
      return Array.isArray(res.logs) ? res.logs : []
    } catch {
      return []
    }
  }

  useEffect(() => {
    if (!playing) {
      setPlaybackMode(false)
      if (playbackTimerRef.current) {
        window.clearInterval(playbackTimerRef.current)
        playbackTimerRef.current = null
      }
      return
    }

    if (!selectedTruck?.booking_id) {
      setPlaying(false)
      return
    }

    let mounted = true
    ;(async () => {
      const requestId = Date.now()
      playbackRequestRef.current = requestId
      const logs = await fetchPlaybackLogs(selectedTruck.booking_id)
      if (!mounted) return
      if (playbackRequestRef.current !== requestId) return
      setPlaybackLogs(logs)
      setPlaybackMode(true)
      playbackIndexRef.current = 0
      setStatusMessage(`Route playback active for ${selectedTruck.lorry_number}.`)

      if (playbackTimerRef.current) {
        window.clearInterval(playbackTimerRef.current)
      }

      playbackTimerRef.current = window.setInterval(() => {
        const idx = playbackIndexRef.current
        if (!logs || logs.length === 0) return
        const entry = logs[idx % logs.length]
        if (entry) {
          const updated = normalizeTruck({
            booking_id: selectedTruck.booking_id,
            lorry_number: selectedTruck.lorry_number,
            latitude: entry.latitude,
            longitude: entry.longitude,
            last_updated: entry.created_at,
            gps_available: true,
          })
          const next = (fleetRef.current || []).slice()
          const j = next.findIndex((t) => t.lorry_number === selectedTruck.lorry_number)
          if (j >= 0) next[j] = { ...next[j], ...updated }
          else next.unshift(updated)
          animateFleetUpdate(next)

          if (entry.event_type === 'delivered' || updated.shipment_status === 'delivered') {
            setTrackingAlerts((prev) => [{ id: `done-${Date.now()}`, type: 'delivery', message: `${selectedTruck.lorry_number} completed delivery playback.` }, ...prev].slice(0, 8))
          }
        }
        playbackIndexRef.current = playbackIndexRef.current + Math.max(1, Math.round(playbackSpeed))
      }, Math.max(200, 1000 / Math.max(0.1, playbackSpeed)))
    })()

    return () => {
      mounted = false
      if (playbackTimerRef.current) {
        window.clearInterval(playbackTimerRef.current)
        playbackTimerRef.current = null
      }
    }
  }, [playing, playbackSpeed, selectedTruck])

  const fetchTruckHistory = async (lorryNumber) => {
    const trimmed = String(lorryNumber || '').trim()
    if (!trimmed) {
      setHistoryLogs([])
      return
    }

    setHistoryLoading(true)

    try {
      const data = await requestJson(`/gps/logs/${encodeURIComponent(trimmed)}?limit=30`)

      setHistoryLogs(Array.isArray(data.logs) ? data.logs : [])
    } catch {
      setHistoryLogs([])
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    fetchFleet()
    const intervalId = window.setInterval(fetchFleet, 5000)

    return () => {
      window.clearInterval(intervalId)
      if (animationFrameRef.current) {
        window.cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [])

  useEffect(() => {
    return () => {
      if (playbackTimerRef.current) {
        window.clearInterval(playbackTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!selectedTruck?.lorry_number) {
      setHistoryLogs([])
      return
    }

    fetchTruckHistory(selectedTruck.lorry_number)
    const intervalId = window.setInterval(() => fetchTruckHistory(selectedTruck.lorry_number), 10000)

    return () => window.clearInterval(intervalId)
  }, [selectedTruck?.lorry_number])

  const handleManualSearch = async () => {
    const trimmed = searchInput.trim()
    if (!trimmed) {
      return
    }

    setSelectedLorryNumber(trimmed)

    const match = visibleFleet.find((truck) => truck.lorry_number.toLowerCase() === trimmed.toLowerCase())
    if (match) {
      setStatusMessage(`Tracking ${match.lorry_number}`)
      return
    }

    try {
      const data = await requestJson(`/gps/trucks/${encodeURIComponent(trimmed)}`)

      const truck = normalizeTruck(data.truck)
      animateFleetUpdate([...visibleFleet.filter((item) => item.lorry_number !== truck.lorry_number), truck])
      setStatusMessage(`Tracking ${truck.lorry_number}`)
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : 'Truck not found')
    }
  }

  const truckCount = visibleFleet.length
  const liveCount = visibleFleet.filter((truck) => truck.gps_available).length
  const selectedSelectedTruck = selectedTruck || null

  return (
    <div className="tracking-page">
      <header className="tracking-hero">
        <div>
          <p className="tracking-kicker">Premium logistics control tower</p>
          <h1>Real-time truck tracking</h1>
          <p className="tracking-hero-copy">
            Monitor multiple trucks on a live Leaflet map, follow their route lines, and watch ETAs update as the fleet moves.
          </p>
        </div>

        <div className="tracking-hero-metrics">
          <article>
            <strong>{truckCount}</strong>
            <span>Active trucks</span>
          </article>
          <article>
            <strong>{liveCount}</strong>
            <span>Live GPS feeds</span>
          </article>
          <article>
            <strong>{selectedSelectedTruck ? formatEta(selectedSelectedTruck.eta_hours) : 'Pending'}</strong>
            <span>Selected ETA</span>
          </article>
        </div>
      </header>

      <section className="tracking-shell">
        <div className="tracking-map-panel">
          <div className="tracking-map-toolbar">
            <div>
              <p className="tracking-toolbar-label">Fleet map</p>
              <h2>{selectedSelectedTruck ? selectedSelectedTruck.lorry_number : 'No truck selected'}</h2>
            </div>
            <div className="tracking-toolbar-actions">
              <button type="button" className="tracking-playback-button" onClick={() => setPlaying((p) => !p)}>
                {playing ? 'Pause Playback' : 'Play Playback'}
              </button>

              <button type="button" className={`tracking-toggle-button ${showTrafficOverlay ? 'is-active' : ''}`} onClick={() => setShowTrafficOverlay((value) => !value)}>
                Traffic
              </button>

              <button type="button" className={`tracking-toggle-button ${showHeatmap ? 'is-active' : ''}`} onClick={() => setShowHeatmap((value) => !value)}>
                Heatmap
              </button>

              <button type="button" className={`tracking-toggle-button ${showGeofences ? 'is-active' : ''}`} onClick={() => setShowGeofences((value) => !value)}>
                Geofences
              </button>

              <label style={{ marginLeft: 8, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                Speed
                <select value={playbackSpeed} onChange={(e) => setPlaybackSpeed(Number(e.target.value))}>
                  <option value={0.5}>0.5x</option>
                  <option value={1}>1x</option>
                  <option value={2}>2x</option>
                  <option value={4}>4x</option>
                </select>
              </label>

              <button type="button" className="tracking-refresh-button" onClick={fetchFleet} disabled={loading}>
                {loading ? 'Refreshing...' : 'Refresh'}
              </button>
            </div>
          </div>

          <div className="tracking-map-frame">
            {visibleFleet.length === 0 && (
              <div className="tracking-empty-overlay">
                <EmptyState
                  title="No live trucks yet"
                  description="The map will populate from real GPS ingest or socket updates as soon as active fleet data is available."
                  accent="blue"
                />
              </div>
            )}
            <MapContainer center={mapCenter} zoom={selectedSelectedTruck ? 10 : defaultZoom} className="tracking-map">
              <MapFocus center={mapCenter} zoom={selectedSelectedTruck ? 10 : defaultZoom} />
              <TileLayer
                url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
              />

              {showHeatmap && heatmapPoints.map((point, index) => {
                const intensity = 0.05 + ((index % 8) * 0.01)
                return (
                  <CircleMarker
                    key={`heat-${index}`}
                    center={[point.lat, point.lng]}
                    radius={8 + (index % 5) * 3}
                    pathOptions={{
                      color: '#fb923c',
                      weight: 0,
                      fillColor: index % 2 === 0 ? '#f97316' : '#38bdf8',
                      fillOpacity: intensity,
                    }}
                  />
                )
              })}

              {showGeofences && geofenceZones.map((zone) => (
                <Circle
                  key={zone.id}
                  center={zone.center}
                  radius={zone.radius}
                  pathOptions={{
                    color: zone.id === 'drop' ? '#38bdf8' : '#f97316',
                    weight: 1,
                    opacity: 0.8,
                    fillColor: zone.id === 'drop' ? '#38bdf8' : '#f97316',
                    fillOpacity: 0.08,
                    dashArray: '8 8',
                  }}
                />
              ))}

              {visibleFleet.map((truck) => {
                const isSelected = truck.lorry_number === selectedSelectedTruck?.lorry_number
                const isDelivered = String(truck.shipment_status || truck.booking_status || '').toLowerCase().includes('deliv')
                const markerIcon = createTruckIcon({ selected: isSelected, label: isDelivered ? '✓' : isSelected ? '🚛' : '▣' })
                const markerPosition = Number.isFinite(truck.latitude) && Number.isFinite(truck.longitude)
                  ? [truck.latitude, truck.longitude]
                  : null

                if (!markerPosition) {
                  return null
                }

                return (
                  <Marker
                    key={truck.lorry_number}
                    position={markerPosition}
                    icon={markerIcon}
                    eventHandlers={{
                      click: () => setSelectedLorryNumber(truck.lorry_number),
                    }}
                  >
                    <Popup>
                      <div className="tracking-popup">
                        <h3>{truck.lorry_number}</h3>
                        <p>
                          <strong>Status:</strong> {truck.booking_status || 'active'}
                        </p>
                        <p>
                          <strong>Truck:</strong> {truck.truck_type || 'Unknown'}
                        </p>
                        <p>
                          <strong>ETA:</strong> {formatEta(truck.eta_hours)}
                        </p>
                        <p>
                          <strong>Distance left:</strong> {formatDistance(truck.distance_km)}
                        </p>
                        <p>
                          <strong>Updated:</strong> {formatTimestamp(truck.last_updated)}
                        </p>
                      </div>
                    </Popup>
                  </Marker>
                )
              })}

              {selectedSelectedTruck?.route_coordinates?.length > 1 && (
                <>
                  <Polyline
                    positions={selectedSelectedTruck.route_coordinates}
                    pathOptions={{ color: '#f97316', weight: 5, opacity: 0.35, dashArray: '14 10' }}
                  />
                  {showTrafficOverlay && (
                    <Polyline
                      positions={selectedSelectedTruck.route_coordinates}
                      pathOptions={{ color: '#fb923c', weight: 3, opacity: 0.92 }}
                    />
                  )}
                </>
              )}

              {selectedTrail.length > 1 && (
                <Polyline
                  positions={selectedTrail}
                  pathOptions={{ color: '#38bdf8', weight: 3, opacity: 0.75, dashArray: '10 10' }}
                />
              )}

              {selectedTrail.slice(-6).map((point, index) => (
                <CircleMarker
                  key={`trail-${index}`}
                  center={point}
                  radius={index === selectedTrail.length - 1 ? 7 : 4}
                  pathOptions={{
                    color: '#38bdf8',
                    weight: 1,
                    fillColor: '#38bdf8',
                    fillOpacity: 0.85,
                  }}
                />
              ))}
            </MapContainer>

            {loading && <div className="tracking-map-loading">Updating live fleet positions...</div>}
            {trackingAlerts.length > 0 && (
              <div className="tracking-alert-stack">
                {trackingAlerts.slice(0, 3).map((alert) => (
                  <div key={alert.id} className={`tracking-alert ${alert.type}`}>
                    <strong>{alert.type === 'delivery' ? 'Delivery complete' : alert.type === 'geofence' ? 'Geofence alert' : 'Stop detected'}</strong>
                    <p>{alert.message}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <aside className="tracking-sidebar">
          <div className="tracking-panel">
            <div className="tracking-panel-header">
              <div>
                <p className="tracking-toolbar-label">Truck search</p>
                <h2>Find a lorry</h2>
              </div>
            </div>

            <div className="tracking-search">
              <label htmlFor="lorrySearch">Lorry number</label>
              <input
                id="lorrySearch"
                type="text"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="Enter lorry number"
              />
              <button type="button" onClick={handleManualSearch} disabled={loading}>
                {loading ? 'Searching...' : 'Track Truck'}
              </button>
            </div>

            <div className="tracking-status-card">
              <p>{statusMessage}</p>
              {error && <p className="tracking-error">{error}</p>}
            </div>
          </div>

          <div className="tracking-panel tracking-fleet-panel">
            <div className="tracking-panel-header">
              <div>
                <p className="tracking-toolbar-label">Live fleet</p>
                <h2>Truck list</h2>
              </div>
            </div>

            <div className="tracking-fleet-list">
              {visibleFleet.length === 0 && <div className="tracking-empty">No live trucks available yet.</div>}

              {visibleFleet.map((truck) => {
                const isSelected = truck.lorry_number === selectedSelectedTruck?.lorry_number

                return (
                  <button
                    key={truck.lorry_number}
                    type="button"
                    className={`fleet-card ${isSelected ? 'is-selected' : ''}`}
                    onClick={() => setSelectedLorryNumber(truck.lorry_number)}
                  >
                    <div className="fleet-card-top">
                      <div>
                        <h3>{truck.lorry_number}</h3>
                        <p>{truck.truck_type || 'Truck'}</p>
                      </div>
                      <span className={`fleet-badge ${truck.gps_available ? 'live' : 'idle'}`}>
                        {truck.gps_available ? 'Live' : 'Idle'}
                      </span>
                    </div>

                    <div className="fleet-card-grid">
                      <div>
                        <span>ETA</span>
                        <strong>{formatEta(truck.eta_hours)}</strong>
                      </div>
                      <div>
                        <span>Distance</span>
                        <strong>{formatDistance(truck.distance_km)}</strong>
                      </div>
                      <div className="fleet-card-wide">
                        <span>Drop</span>
                        <strong>{truck.drop_location || 'Pending'}</strong>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="tracking-panel tracking-detail-panel">
            <div className="tracking-panel-header">
              <div>
                <p className="tracking-toolbar-label">Selected truck</p>
                <h2>Details</h2>
              </div>
            </div>

            {selectedSelectedTruck ? (
              <div className="tracking-detail-grid">
                <div>
                  <span>Booking</span>
                  <strong>#{selectedSelectedTruck.booking_id}</strong>
                </div>
                <div>
                  <span>Status</span>
                  <strong>{selectedSelectedTruck.booking_status || 'active'}</strong>
                </div>
                <div>
                  <span>Latitude</span>
                  <strong>{Number(selectedSelectedTruck.latitude).toFixed(4)}</strong>
                </div>
                <div>
                  <span>Longitude</span>
                  <strong>{Number(selectedSelectedTruck.longitude).toFixed(4)}</strong>
                </div>
                <div>
                  <span>Updated</span>
                  <strong>{formatTimestamp(selectedSelectedTruck.last_updated)}</strong>
                </div>
                <div>
                  <span>History points</span>
                  <strong>{historyLoading ? 'Loading...' : historyLogs.length}</strong>
                </div>
                <div>
                  <span>Playback</span>
                  <strong>{playbackMode ? `${playbackIndex + 1}/${Math.max(playbackLogs.length, 1)}` : 'Idle'}</strong>
                </div>
                <div>
                  <span>Stop status</span>
                  <strong>{stopDetection ? 'Stopped' : 'Moving'}</strong>
                </div>
                <div>
                  <span>Heartbeat</span>
                  <strong>{selectedSelectedTruck.heartbeat?.status || 'Unknown'}</strong>
                </div>
                <div>
                  <span>Speed</span>
                  <strong>{selectedSelectedTruck.speed_kmph === null || selectedSelectedTruck.speed_kmph === undefined ? 'Pending' : `${Number(selectedSelectedTruck.speed_kmph).toFixed(0)} km/h`}</strong>
                </div>
                <div>
                  <span>Route progress</span>
                  <strong>{selectedSelectedTruck.route_progress?.percent === undefined ? 'Pending' : `${selectedSelectedTruck.route_progress.percent}%`}</strong>
                </div>
                <div>
                  <span>Traffic</span>
                  <strong>{selectedSelectedTruck.traffic?.level || 'Clear'}</strong>
                </div>
              </div>
            ) : (
              <div className="tracking-empty">Select a truck to inspect live GPS movement.</div>
            )}

            <div className="tracking-playback-summary">
              <p>{playbackMode ? 'Route playback running from historical GPS logs.' : 'Use playback to replay a shipment route.'}</p>
              <div className="tracking-playback-actions">
                <button type="button" onClick={() => setPlaying((value) => !value)}>
                  {playing ? 'Pause' : 'Start'}
                </button>
                <button type="button" onClick={() => { setPlaying(false); setPlaybackMode(false); setPlaybackIndex(0); }}>
                  Reset
                </button>
              </div>
            </div>
          </div>
        </aside>
      </section>
    </div>
  )
}
