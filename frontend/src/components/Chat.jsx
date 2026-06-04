import { useState, useRef, useEffect } from 'react'
import { io } from 'socket.io-client'
import { sendMessage } from '../services/chatService'
import { useAuth } from '../context/AuthContext'
import { calculateDistance, calculatePrice, extractDistance } from '../utils/transportUtils'
import { API_BASE_URL, SOCKET_BASE_URL } from '../services/apiBase'
import './Chat.css'

const INTENT_LABELS = {
  BOOK_SHIPMENT: 'Booking',
  TRACK_SHIPMENT: 'Tracking',
  GET_PRICE_ESTIMATE: 'Price estimate',
  MAKE_PAYMENT: 'Payment',
  GET_ANALYTICS: 'Analytics',
  DRIVER_UPDATE: 'Driver update',
  CUSTOMER_SUPPORT: 'Support',
  UNRELATED: 'General',
}

const WORKFLOW_LABELS = {
  booking: 'Shipment workflow',
  tracking: 'Live tracking',
  pricing: 'Fare estimate',
  payments: 'Payment flow',
  analytics: 'Admin analytics',
  driver_management: 'Driver management',
  support: 'Customer support',
  fallback: 'Assistant',
}

const CARD_LABELS = {
  tracking_card: 'Shipment tracking',
  payment_card: 'Payment',
  invoice_card: 'Invoice',
  booking_summary_card: 'Booking summary',
  analytics_card: 'Analytics snapshot',
  driver_assignment_card: 'Driver assignment',
  eta_status_card: 'ETA status',
  delivery_confirmation_card: 'Delivery confirmation',
  delay_alert_card: 'Delay alert',
}

const ROLE_QUICK_ACTIONS = {
  customer: [
    { label: 'Book shipment', prompt: 'I want to book a shipment', mode: 'send' },
    { label: 'Track shipment', prompt: 'Where is my shipment?', mode: 'send' },
    { label: 'Get quote', prompt: 'Give me a price estimate', mode: 'send' },
    { label: 'Make payment', prompt: 'Show my payment status', mode: 'send' },
  ],
  admin: [
    { label: 'Analytics', prompt: 'Show today analytics', mode: 'send' },
    { label: 'Delayed shipments', prompt: 'Show delayed shipments', mode: 'send' },
    { label: 'Revenue reports', prompt: 'Show revenue report', mode: 'send' },
    { label: 'Driver management', prompt: 'Manage drivers', mode: 'send' },
  ],
  driver: [
    { label: 'Assigned shipments', prompt: 'Show my assigned shipments', mode: 'send' },
    { label: 'Start trip', prompt: 'Start trip for my assigned shipment', mode: 'send' },
    { label: 'Update status', prompt: 'Update shipment status', mode: 'send' },
    { label: 'Upload POD', prompt: 'Upload proof of delivery', mode: 'send' },
  ],
}

const ROLE_SUGGESTIONS = {
  customer: 'Booking, tracking, payments, and support',
  admin: 'Analytics, delayed shipments, revenue reports, and driver management',
  driver: 'Assigned shipments, trip updates, status changes, and POD upload',
}

const formatMoney = (value) => {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '₹0'
  return `₹${Math.round(amount).toLocaleString('en-IN')}`
}

const formatMetric = (value, suffix = '') => {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  return `${Math.round(amount).toLocaleString('en-IN')}${suffix}`
}

const formatRelativeEta = (hours) => {
  const value = Number(hours)
  if (!Number.isFinite(value)) return 'ETA unavailable'
  if (value <= 0) return 'Arriving soon'
  if (value < 1) return 'Less than 1 hour'
  if (value === 1) return '1 hour'
  return `${Math.round(value)} hours`
}

const getMessageCardType = (msg) => String(msg?.card_type || msg?.type || msg?.meta?.card_type || msg?.meta?.type || '').trim()

const getCardTitle = (cardType, fallbackTitle) => CARD_LABELS[cardType] || fallbackTitle || 'Transport update'

const normalizeRoutePoint = (point) => {
  if (Array.isArray(point)) {
    return [Number(point[0]), Number(point[1])]
  }

  if (point && typeof point === 'object') {
    return [Number(point.latitude ?? point.lat), Number(point.longitude ?? point.lng)]
  }

  return [Number.NaN, Number.NaN]
}

const buildMiniRoutePolyline = (points) => {
  const normalized = (Array.isArray(points) ? points : [])
    .map(normalizeRoutePoint)
    .filter(([latitude, longitude]) => Number.isFinite(latitude) && Number.isFinite(longitude))

  if (normalized.length === 0) {
    return { points: [], first: null, last: null }
  }

  const latitudes = normalized.map(([latitude]) => latitude)
  const longitudes = normalized.map(([, longitude]) => longitude)
  const minLat = Math.min(...latitudes)
  const maxLat = Math.max(...latitudes)
  const minLng = Math.min(...longitudes)
  const maxLng = Math.max(...longitudes)
  const spanLat = Math.max(maxLat - minLat, 0.0001)
  const spanLng = Math.max(maxLng - minLng, 0.0001)

  const scaledPoints = normalized.map(([latitude, longitude]) => {
    const x = 10 + ((longitude - minLng) / spanLng) * 80
    const y = 90 - ((latitude - minLat) / spanLat) * 80
    return [Number(x.toFixed(2)), Number(y.toFixed(2))]
  })

  return {
    points: scaledPoints,
    first: scaledPoints[0],
    last: scaledPoints[scaledPoints.length - 1],
  }
}

export default function Chat({ source = '', destination = '', distanceMessage = '' }) {
  const { user } = useAuth()
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [isEstimating, setIsEstimating] = useState(false)
  const [isPaying, setIsPaying] = useState(false)
  const [assistantDockOpen, setAssistantDockOpen] = useState(false)
  const [distance, setDistance] = useState(null)
  const [price, setPrice] = useState(null)
  const [chatSource, setChatSource] = useState(null)
  const [chatDestination, setChatDestination] = useState(null)
  const [calculatingDistance, setCalculatingDistance] = useState(false)
  const [pendingMessage, setPendingMessage] = useState(null) // legacy
  const [expectingTons, setExpectingTons] = useState(false)
  const [pendingTons, setPendingTons] = useState(null)
  const [awaitingBookingDecision, setAwaitingBookingDecision] = useState(false)
  const [isRestartingAfterBooking, setIsRestartingAfterBooking] = useState(false)
  const [isFirstMessage, setIsFirstMessage] = useState(true)
  const [sendError, setSendError] = useState('')
  const [retryDraft, setRetryDraft] = useState('')
  const [liveTrackingByLorry, setLiveTrackingByLorry] = useState({})
  const [realtimeFeed, setRealtimeFeed] = useState([])
  const [expandedMessages, setExpandedMessages] = useState({})
  const [loadingActions, setLoadingActions] = useState({})
  const [streamingMessagesState, setStreamingMessagesState] = useState({})
  const [fareEstimateForm, setFareEstimateForm] = useState({
    pickup_location: '',
    drop_location: '',
    load_weight: '',
    truck_type: '12 tyre',
  })
  const messagesEndRef = useRef(null)
  const userIdRef = useRef(null)
  const isSendingRef = useRef(false)
  const welcomeShownRef = useRef(false)
  const [welcomeLoading, setWelcomeLoading] = useState(true)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const getTruckTypeFromTons = (tonsValue) => {
    if (tonsValue >= 20 && tonsValue <= 24) return '12 tyre'
    if (tonsValue >= 25 && tonsValue <= 29) return '14 tyre'
    if (tonsValue >= 30 && tonsValue <= 35) return '16 tyre'
    return ''
  }

  const mergeLiveTrackingSnapshot = (payload = {}, eventName = 'tracking:update') => {
    const shipment = payload?.shipment || payload?.snapshot?.shipment || payload?.booking || null
    const lorryNumber = String(
      payload?.lorry_number
      || shipment?.lorry_number
      || payload?.snapshot?.shipment?.lorry_number
      || shipment?.vehicle_number
      || '',
    ).trim()

    setLiveTrackingByLorry((previous) => {
      const nextSnapshot = { ...previous }

      if (Array.isArray(payload?.trucks)) {
        payload.trucks.forEach((truck) => {
          const truckLorryNumber = String(truck?.lorry_number || '').trim()
          if (truckLorryNumber) {
            nextSnapshot[truckLorryNumber] = truck
          }
        })
      }

      if (lorryNumber) {
        const existing = nextSnapshot[lorryNumber] || {}
        nextSnapshot[lorryNumber] = {
          ...existing,
          ...payload?.snapshot,
          lorry_number: lorryNumber,
          live_location: payload?.live_location || payload?.latest_location || existing.live_location || null,
          latest_location: payload?.latest_location || payload?.live_location || existing.latest_location || null,
          route_coordinates: Array.isArray(payload?.route_coordinates) ? payload.route_coordinates : existing.route_coordinates || [],
          eta_hours: payload?.eta_hours ?? existing.eta_hours ?? null,
          route_source: payload?.route_source || existing.route_source || 'planned',
          shipment: shipment || existing.shipment || null,
          summary: payload?.summary || existing.summary || null,
          timeline: Array.isArray(payload?.timeline) ? payload.timeline : existing.timeline || [],
          last_event: eventName,
        }
      }

      return nextSnapshot
    })
  }

  const addRealtimeEvent = (event) => {
    const nextEvent = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      timestamp: new Date().toISOString(),
      level: 'info',
      ...event,
    }

    setRealtimeFeed((previous) => [nextEvent, ...previous].slice(0, 10))
    return nextEvent
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined
    }

    const socket = io(SOCKET_BASE_URL, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
    })

    const realtimeEvents = [
      'fleet:update',
      'truck:location',
      'tracking:update',
      'shipment:update',
      'shipment:activity',
      'payment:update',
      'driver:update',
      'eta:update',
      'delivery:confirmed',
      'notification:update',
      'monitoring:alert',
    ]

    socket.on('fleet:update', (payload) => {
      mergeLiveTrackingSnapshot(payload, 'fleet:update')
      addRealtimeEvent({
        title: 'Fleet snapshot refreshed',
        message: `Tracking ${Number(payload?.count || 0)} truck(s) from live GPS data.`,
        level: 'info',
        type: 'fleet:update',
        data: payload,
      })
    })

    socket.on('truck:location', (payload) => {
      mergeLiveTrackingSnapshot({
        ...payload,
        live_location: payload,
        latest_location: payload,
      }, 'truck:location')
      addRealtimeEvent({
        title: 'Truck moved',
        message: `${String(payload?.lorry_number || 'Truck')} is now at ${Number(payload?.latitude || 0).toFixed(4)}, ${Number(payload?.longitude || 0).toFixed(4)}.`,
        level: 'info',
        type: 'truck:location',
        data: payload,
      })
    })

    realtimeEvents.slice(2).forEach((eventName) => {
      socket.on(eventName, (payload) => {
        mergeLiveTrackingSnapshot(payload, eventName)
        addRealtimeEvent({
          title: eventName.replace(/[:_]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()),
          message: payload?.message || payload?.status_note || payload?.note || payload?.title || 'Live update received.',
          level: eventName === 'monitoring:alert' ? (payload?.level || 'warning') : 'info',
          type: eventName,
          data: payload,
        })
      })
    })

    socket.on('chat:update', (payload) => {
      addRealtimeEvent({
        title: 'Assistant update',
        message: payload?.reply || payload?.message || 'Live logistics update received.',
        level: payload?.status === 'error' ? 'warning' : 'info',
        type: 'chat:update',
        data: payload,
      })

      if (payload?.reply || payload?.message) {
        appendMessage(payload.reply || payload.message, 'bot', {
          meta: payload,
          suggestions: Array.isArray(payload?.suggestions) ? payload.suggestions : [],
        })
      }
    })

    // Streaming AI events
    socket.on('ai:stream:start', (payload) => {
      try {
        const { stream_id } = payload || {}
        if (!stream_id) return
        setMessages((prev) => [...prev, { id: stream_id, text: '', sender: 'bot', is_streaming: true, meta: payload }])
        setStreamingMessagesState((prev) => ({ ...prev, [stream_id]: { text: '', started_at: Date.now() } }))
        scrollToBottom()
      } catch (e) {
        // ignore
      }
    })

    socket.on('ai:stream:chunk', (payload) => {
      try {
        const { stream_id, chunk } = payload || {}
        if (!stream_id) return
        // update existing message with id == stream_id, or fallback to last bot message
        setMessages((prev) => {
          let found = false
          const next = prev.map((m) => {
            if (m.id === stream_id) {
              found = true
              return { ...m, text: String((m.text || '') + (chunk || '')), is_streaming: true }
            }
            return m
          })
          if (!found) {
            // find last bot message and append
            const lastBotIndex = prev.map((m) => m.sender).lastIndexOf('bot')
            if (lastBotIndex >= 0) {
              const copy = [...prev]
              const m = copy[lastBotIndex]
              copy[lastBotIndex] = { ...m, text: String((m.text || '') + (chunk || '')), is_streaming: true }
              return copy
            }
            // otherwise append new streaming message with stream_id
            return [...prev, { id: stream_id, text: String(chunk || ''), sender: 'bot', is_streaming: true, meta: payload }]
          }
          return next
        })
        scrollToBottom()
      } catch (e) {
        // ignore
      }
    })

    socket.on('ai:stream:end', (payload) => {
      try {
        const { stream_id, text, status, error } = payload || {}
        setMessages((prev) => prev.map((m) => (m.id === stream_id ? { ...m, text: String(text || m.text || ''), is_streaming: false, meta: { ...(m.meta || {}), ...(payload || {}) } } : m)))
        // cleanup streaming state
        setStreamingMessagesState((prev) => {
          const next = { ...prev }
          delete next[stream_id]
          return next
        })
        scrollToBottom()
      } catch (e) {
        // ignore
      }
    })

    return () => {
      realtimeEvents.forEach((eventName) => socket.off(eventName))
      socket.off('chat:update')
      socket.disconnect()
    }
  }, [])

  const formatIntentLabel = (intent) => INTENT_LABELS[String(intent || '').trim().toUpperCase()] || String(intent || 'General')

  const formatWorkflowLabel = (workflow) => WORKFLOW_LABELS[String(workflow || '').trim()] || String(workflow || '')

  const handleCardAction = (action, msg) => {
    const kind = String(action?.kind || 'send').trim().toLowerCase()

    if (kind === 'payment') {
      openPaymentCheckout(msg?.meta?.data || msg?.data || {})
      return
    }

    if (kind === 'open_url') {
      const targetUrl = String(action?.url || msg?.meta?.data?.download_url || msg?.data?.download_url || '').trim()
      if (!targetUrl) return
      const absoluteUrl = targetUrl.startsWith('http') ? targetUrl : `${API_BASE_URL}${targetUrl}`
      window.open(absoluteUrl, '_blank', 'noopener,noreferrer')
      return
    }

    const prompt = String(action?.prompt || action?.label || '').trim()
    if (prompt) {
      handleSendMessage({ messageText: prompt, echoUserMessage: true })
    }
  }

  const toggleExpand = (msgId) => {
    setExpandedMessages((prev) => ({ ...prev, [msgId]: !prev[msgId] }))
  }

  const handleRetryAction = async (action, msg) => {
    const key = msg.id || `${Date.now()}`
    setLoadingActions((prev) => ({ ...prev, [key]: true }))
    try {
      if (action.retry_endpoint) {
        const endpoint = action.retry_endpoint.startsWith('http') ? action.retry_endpoint : `${API_BASE_URL}${action.retry_endpoint}`
        const body = action.payload || msg?.meta?.data || msg?.data || {}
        const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        const data = await res.json().catch(() => ({}))
        const replyText = data?.reply || data?.message || (res.ok ? 'Retry action completed' : 'Retry failed')
        appendMessage(replyText, 'bot', { meta: data })
      } else if (action.prompt) {
        // fallback to resend prompt through chat pipeline
        handleSendMessage({ messageText: String(action.prompt).trim(), echoUserMessage: true })
      } else if (action.label) {
        // fallback: resend original message text
        handleSendMessage({ messageText: msg.text || action.label, echoUserMessage: true })
      }
    } catch (err) {
      appendMessage(`Retry failed: ${err?.message || String(err)}`, 'bot')
    } finally {
      setLoadingActions((prev) => ({ ...prev, [key]: false }))
    }
  }

  // Structured details renderers
  const renderStatusBadge = (status) => {
    const s = String(status || '').toLowerCase()
    const cls = s.includes('deliv') || s.includes('completed') || s.includes('captured') ? 'transport-card-chip-success' : s.includes('delay') || s.includes('failed') || s.includes('error') ? 'transport-card-chip-warning' : 'transport-card-chip-muted'
    return <span className={`transport-card-chip ${cls}`}>{String(status || 'Unknown')}</span>
  }

  const renderMoney = (amount) => {
    try {
      const value = Number(amount || 0)
      return formatMoney(value / 100 || value)
    } catch (e) {
      return formatMoney(amount)
    }
  }

  const renderTimeline = (timeline = []) => {
    if (!Array.isArray(timeline) || timeline.length === 0) return <div className="transport-card-timeline-empty">No timeline entries</div>
    return (
      <div className="transport-card-timeline">
        {timeline.map((step, idx) => (
          <div key={`${idx}-${step.id || idx}`} className={`transport-card-timeline-item ${step.status || ''}`}>
            <span className="timeline-time">{step.created_at ? new Date(step.created_at).toLocaleString() : ''}</span>
            <div className="timeline-body">
              <strong>{step.status || step.note || 'Update'}</strong>
              <div className="timeline-note">{step.note || step.location || ''}</div>
            </div>
          </div>
        ))}
      </div>
    )
  }

  const renderShipmentSummary = (shipment = {}) => {
    const pickup = shipment.pickup_location || shipment.source || shipment.pickup_address || ''
    const drop = shipment.drop_location || shipment.destination || shipment.delivery_address || ''
    const driver = shipment.driver || shipment.assigned_driver || {}
    return (
      <div className="details-shipment">
        <div className="details-row">
          <div><span>Shipment</span><strong>#{shipment.id || shipment.booking_ref || '—'}</strong></div>
          <div>{renderStatusBadge(shipment.shipment_status || shipment.status)}</div>
        </div>
        <div className="details-grid">
          <div><span>Pickup</span><strong>{pickup}</strong></div>
          <div><span>Drop</span><strong>{drop}</strong></div>
          <div><span>Truck</span><strong>{shipment.truck_type || shipment.lorry_type || '—'}</strong></div>
          <div><span>Weight</span><strong>{shipment.weight || shipment.weight_kg || shipment.load_weight || '—'}</strong></div>
        </div>
        <div className="details-row">
          <div><span>Driver</span>
            <div className="driver-contact">
              <strong>{driver.driver_name || driver.name || 'Unassigned'}</strong>
              {driver.phone ? <a href={`tel:${driver.phone}`} className="driver-phone">{driver.phone}</a> : null}
            </div>
          </div>
          <div><span>ETA</span><strong>{shipment.eta_hours ? formatRelativeEta(shipment.eta_hours) : (shipment.delivered_at ? `Delivered ${new Date(shipment.delivered_at).toLocaleString()}` : '—')}</strong></div>
        </div>
        {renderTimeline(shipment.timeline || shipment.events || [])}
      </div>
    )
  }

  const renderInvoice = (invoice = {}) => {
    const items = invoice.items || invoice.lines || []
    return (
      <div className="details-invoice">
        <div className="details-row">
          <div><span>Invoice</span><strong>{invoice.invoice_number || invoice.invoice_no || 'Pending'}</strong></div>
          <div>{renderStatusBadge(invoice.status || invoice.payment_status)}</div>
        </div>
        <div className="invoice-items">
          {items.length === 0 ? (
            <div className="invoice-empty">No line items</div>
          ) : (
            <table className="invoice-table">
              <thead><tr><th>Item</th><th>Qty</th><th>Rate</th><th>Total</th></tr></thead>
              <tbody>
                {items.map((it, i) => (
                  <tr key={i}><td>{it.description || it.name || `Line ${i+1}`}</td><td>{it.quantity || it.qty || 1}</td><td>{renderMoney(it.unit_amount || it.rate || it.price || 0)}</td><td>{renderMoney(it.total || it.amount || (it.unit_amount || 0) * (it.quantity || it.qty || 1))}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="details-row details-summary">
          <div><span>Subtotal</span><strong>{renderMoney(invoice.subtotal || invoice.total_before_tax || 0)}</strong></div>
          <div><span>Tax</span><strong>{renderMoney(invoice.tax || invoice.gst || 0)}</strong></div>
          <div><span>Total</span><strong>{renderMoney(invoice.total_amount || invoice.total || invoice.amount || 0)}</strong></div>
        </div>
      </div>
    )
  }

  const renderPaymentSummary = (payment = {}) => {
    return (
      <div className="details-payment">
        <div className="details-row">
          <div><span>Amount</span><strong>{renderMoney(payment.amount || payment.total || 0)}</strong></div>
          <div>{renderStatusBadge(payment.status || payment.payment_status)}</div>
        </div>
        <div className="details-grid">
          <div><span>Method</span><strong>{payment.method || payment.payment_method || '—'}</strong></div>
          <div><span>Order</span><strong>{payment.order_id || payment.razorpay_order_id || '—'}</strong></div>
          <div><span>Txn</span><strong>{payment.payment_id || payment.razorpay_payment_id || '—'}</strong></div>
          <div><span>Processed</span><strong>{payment.processed_at ? new Date(payment.processed_at).toLocaleString() : '—'}</strong></div>
        </div>
      </div>
    )
  }

  const renderDriverCard = (driver = {}) => {
    return (
      <div className="details-driver">
        <div className="details-row">
          <div><strong>{driver.driver_name || driver.name || 'Driver'}</strong></div>
          <div>{renderStatusBadge(driver.status)}</div>
        </div>
        <div className="details-grid">
          <div><span>Phone</span><strong>{driver.phone || '—'}</strong></div>
          <div><span>License</span><strong>{driver.license_number || '—'}</strong></div>
          <div><span>Truck</span><strong>{driver.assigned_truck || driver.assigned_truck_type || '—'}</strong></div>
          <div><span>Rating</span><strong>{Number.isFinite(Number(driver.rating)) ? Number(driver.rating).toFixed(1) : '—'}</strong></div>
        </div>
      </div>
    )
  }

  const renderRouteSummary = (route = {}) => {
    const coords = route.route_coordinates || route.coordinates || []
    const points = Array.isArray(coords) ? coords.length : 0
    return (
      <div className="details-route">
        <div className="details-row">
          <div><span>Route</span><strong>{route.name || (points ? `${points} points` : 'No route')}</strong></div>
          <div><span>Distance</span><strong>{formatMetric(route.distance || route.distance_km || route.distance_m || 0, ' km')}</strong></div>
        </div>
        {points > 0 && (
          <div className="details-small">Start: {String(coords[0])} • End: {String(coords[coords.length - 1])}</div>
        )}
      </div>
    )
  }

  const renderDetails = (msg) => {
    const data = msg?.meta?.data || msg?.data || {}
    if (!data || Object.keys(data).length === 0) return <pre className="transport-card-details">No details available</pre>

    // detect types
    if (data.shipment || data.shipping || data.booking) return renderShipmentSummary(data.shipment || data.shipping || data.booking)
    if (data.invoice || data.invoice_number) return renderInvoice(data.invoice || data)
    if (data.payment || data.latest_payment || data.amount) return renderPaymentSummary(data.payment || data.latest_payment || data)
    if (data.driver || data.driver_name) return renderDriverCard(data.driver || data)
    if (data.timeline || data.steps) return renderTimeline(data.timeline || data.steps)
    if (data.route_coordinates || data.coordinates) return renderRouteSummary(data)

    // fallback: pretty JSON
    return <pre className="transport-card-details">{JSON.stringify(data, null, 2)}</pre>
  }

  const renderCardActions = (actions, msg) => {
    if (!Array.isArray(actions) || actions.length === 0) {
      return null
    }

    return (
      <div>
        <div className="transport-card-action-row message-suggestions">
        {actions.slice(0, 4).map((action) => {
          const isRetry = String((action.kind || '')).toLowerCase() === 'retry'
          return (
            <button
              key={action.key || action.label}
              type="button"
              className={`message-suggestion-chip ${isRetry ? 'is-retry' : ''}`}
              onClick={() => (isRetry ? handleRetryAction(action, msg) : handleCardAction(action, msg))}
              disabled={Boolean(loadingActions[msg.id || ''])}
            >
              {loadingActions[msg.id || ''] && isRetry ? 'Retrying...' : action.label || 'Action'}
            </button>
          )
        })}
        {/* Expand details button when data contains verbose details */}
        {(msg?.meta?.data?.details || msg?.data?.details || msg?.meta?.details) && (
          <button type="button" className="message-suggestion-chip" onClick={() => toggleExpand(msg.id)}>
            {expandedMessages[msg.id] ? 'Hide details' : 'View details'}
          </button>
        )}
      </div>
        {expandedMessages[msg.id] && (
          <div className={`transport-card-details details-collapse ${expandedMessages[msg.id] ? 'is-open' : ''}`}>
            {renderDetails(msg)}
          </div>
        )}
      </div>
    )
  }


  const role = String(user?.role || 'customer').trim().toLowerCase() || 'customer'
  const roleQuickActions = ROLE_QUICK_ACTIONS[role] || ROLE_QUICK_ACTIONS.customer
  const roleSuggestionText = ROLE_SUGGESTIONS[role] || ROLE_SUGGESTIONS.customer

  const loadRazorpayScript = () => {
    if (typeof window === 'undefined') return Promise.reject(new Error('Razorpay is unavailable in this environment.'))
    if (window.Razorpay) return Promise.resolve(true)

    return new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = 'https://checkout.razorpay.com/v1/checkout.js'
      script.onload = () => resolve(true)
      script.onerror = () => reject(new Error('Unable to load Razorpay checkout.'))
      document.body.appendChild(script)
    })
  }

  const handlePaymentSuccess = async (payload, responseData) => {
    const verifyPayload = {
      booking_id: payload.booking_id,
      shipment_id: payload.shipment_id,
      razorpay_order_id: responseData.razorpay_order_id,
      razorpay_payment_id: responseData.razorpay_payment_id,
      razorpay_signature: responseData.razorpay_signature,
      payment_type: payload.payment_type || 'advance',
    }

    const verifyResponse = await fetch(`${API_BASE_URL}/payments/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(verifyPayload),
    })

    const verifyData = await verifyResponse.json().catch(() => ({}))

    if (!verifyResponse.ok) {
      throw new Error(verifyData.message || 'Payment verification failed.')
    }

    appendMessage(verifyData.message || 'Payment verified successfully.', 'bot', {
      variant: 'summary',
      meta: {
        intent: 'MAKE_PAYMENT',
        workflow: 'payments',
        confidence: 1,
        data: verifyData.payment || payload,
      },
    })
  }

  const openPaymentCheckout = async (paymentData) => {
    if (isPaying) return

    setIsPaying(true)
    try {
      await loadRazorpayScript()

      const options = {
        key: paymentData.key_id,
        amount: paymentData.amount,
        currency: paymentData.currency || 'INR',
        name: 'SKDLS Transportations',
        description: 'Shipment payment',
        order_id: paymentData.order_id,
        prefill: {
          name: 'Customer',
          email: '',
          contact: '',
        },
        theme: {
          color: '#2563eb',
        },
        handler: async (responseData) => {
          await handlePaymentSuccess(paymentData, responseData)
        },
      }

      const checkout = new window.Razorpay(options)
      checkout.on('payment.failed', (failure) => {
        appendMessage(failure?.error?.description || 'Payment failed. Please try again.', 'bot', { variant: 'error' })
      })
      checkout.open()
    } catch (error) {
      appendMessage(error instanceof Error ? error.message : 'Unable to start payment.', 'bot', { variant: 'error' })
    } finally {
      setIsPaying(false)
    }
  }

  // Initialize persistent user_id and fetch welcome once
  useEffect(() => {
    if (!userIdRef.current) {
      let id = localStorage.getItem('chat_user_id')
      if (!id) {
        id = `user_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
        localStorage.setItem('chat_user_id', id)
      }
      userIdRef.current = id
    }

    if (welcomeShownRef.current) return
    welcomeShownRef.current = true

    let mounted = true
    const fetchWelcome = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/chat/welcome?user_id=${userIdRef.current}`)
        if (!mounted) return
        if (!res.ok) throw new Error('non-200')
        const data = await res.json()
        const welcome = typeof data.reply === 'string' ? data.reply : ''
        if (welcome) {
          // Only append if there are no messages yet to avoid duplicates
          setMessages((prev) => (prev.length === 0 ? [{ id: `welcome-${Date.now()}`, text: welcome, sender: 'bot' }] : prev))
          setIsFirstMessage(false)
          setWelcomeLoading(false)
          return
        }
      } catch (err) {
        if (!mounted) return
        setMessages((prev) => (prev.length === 0 ? [{ id: `welcome-fallback-${Date.now()}`, text: 'Hello! Welcome to SKDLS Transportations.', sender: 'bot' }] : prev))
        setIsFirstMessage(false)
        setWelcomeLoading(false)
      }
    }

    fetchWelcome()
    return () => {
      mounted = false
    }
  }, [])

  const appendMessage = (text, sender, options = {}) => {
    const nextMessage = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      text,
      sender,
      variant: options.variant || 'default',
      type: options.type || options.meta?.type || '',
      data: options.data || options.meta?.data || null,
      meta: options.meta || null,
      suggestions: Array.isArray(options.suggestions) ? options.suggestions : [],
    }

    setMessages((prev) => [...prev, nextMessage])
  }

  const renderChatCard = (msg) => {
    const cardType = getMessageCardType(msg)
    const cardData = msg?.data || msg?.meta?.data || {}

    if (!cardType) {
      return null
    }

    if (cardType === 'tracking_card' || cardType === 'delivery_confirmation_card' || cardType === 'delay_alert_card') {
      const shipment = cardData.shipment || {}
      const liveSnapshot = shipment.lorry_number ? liveTrackingByLorry[shipment.lorry_number] : null
      const latestLocation = liveSnapshot?.live_location || cardData.latest_location || cardData.live_location || {}
      const timeline = Array.isArray(liveSnapshot?.timeline) && liveSnapshot.timeline.length > 0
        ? liveSnapshot.timeline
        : Array.isArray(cardData.timeline)
          ? cardData.timeline
          : []
      const routeCoordinates = Array.isArray(liveSnapshot?.route_coordinates) && liveSnapshot.route_coordinates.length > 0
        ? liveSnapshot.route_coordinates
        : Array.isArray(cardData.route_coordinates)
          ? cardData.route_coordinates
          : []
      const etaHours = liveSnapshot?.eta_hours ?? cardData.eta_hours ?? null
      const routeSource = liveSnapshot?.route_source || cardData.route_source || (routeCoordinates.length > 1 ? 'planned' : 'gps')
      const liveStatus = liveSnapshot?.summary?.status || shipment.shipment_status || 'Pending'
      const miniMap = buildMiniRoutePolyline(routeCoordinates.length > 1 ? routeCoordinates : [latestLocation, shipment.drop_location ? shipment.drop_location : null])
      const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []
      const isDeliveryCard = cardType === 'delivery_confirmation_card'
      const isDelayCard = cardType === 'delay_alert_card'
      const trackingKicker = isDeliveryCard ? 'Delivery update' : isDelayCard ? 'Delay alert' : 'Live shipment'
      const chipLabel = isDelayCard ? 'Delayed' : liveStatus

      return (
        <>
          <div className="transport-card transport-card-tracking">
            <div className="transport-card-header">
              <div>
                <p className="transport-card-kicker">{trackingKicker}</p>
                <h3>{getCardTitle(cardType, 'Shipment tracking')}</h3>
              </div>
              <span className="transport-card-chip">{chipLabel}</span>
            </div>

          <div className="transport-card-grid transport-card-grid-compact">
            <div>
              <span>Shipment</span>
              <strong>#{shipment.id || '—'}</strong>
            </div>
            <div>
              <span>Driver</span>
              <strong>{liveSnapshot?.shipment?.driver_name || cardData.driver?.driver_name || 'Unassigned'}</strong>
            </div>
            <div>
              <span>Pickup</span>
              <strong>{shipment.pickup_location || 'Awaiting pickup'}</strong>
            </div>
            <div>
              <span>Drop</span>
              <strong>{shipment.drop_location || 'Awaiting drop'}</strong>
            </div>
          </div>

          <div className="transport-card-flow">
            <div className="transport-card-flow-header">
              <span>Tracking progress</span>
              <strong>{etaHours !== null ? `ETA ${formatRelativeEta(etaHours)}` : `${timeline.length ? `${timeline.length} updates` : 'No updates yet'}`}</strong>
            </div>
            <div className="transport-card-flow-bar">
              <span style={{ width: `${Math.min(100, Math.max(20, (timeline.length || 1) * 18))}%` }} />
            </div>
          </div>

          <div className="transport-card-grid">
            <div>
              <span>Latest location</span>
              <strong>
                {Number.isFinite(Number(latestLocation.latitude)) && Number.isFinite(Number(latestLocation.longitude))
                  ? `${Number(latestLocation.latitude).toFixed(4)}, ${Number(latestLocation.longitude).toFixed(4)}`
                  : 'GPS pending'}
              </strong>
            </div>
            <div>
              <span>Route</span>
              <strong>{routeCoordinates.length > 1 ? `${routeSource === 'live' ? 'Live' : 'Planned'} route ready` : 'Tracking route not assigned'}</strong>
            </div>
          </div>

          {routeCoordinates.length > 1 && miniMap.points.length > 1 && (
            <div className="transport-card-map">
              <div className="transport-card-map-header">
                <span>Mini route map</span>
                <strong>{routeSource === 'live' ? 'GPS synced' : 'Planned route'}</strong>
              </div>
              <svg className="transport-card-map-svg" viewBox="0 0 100 100" role="img" aria-label="Mini route map">
                <defs>
                  <linearGradient id="routeGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#f97316" />
                    <stop offset="100%" stopColor="#fb923c" />
                  </linearGradient>
                </defs>
                <polyline
                  points={miniMap.points.map(([x, y]) => `${x},${y}`).join(' ')}
                  fill="none"
                  stroke="url(#routeGradient)"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {miniMap.first && (
                  <circle cx={miniMap.first[0]} cy={miniMap.first[1]} r="4" className="transport-card-map-marker transport-card-map-marker-start" />
                )}
                {miniMap.last && (
                  <circle cx={miniMap.last[0]} cy={miniMap.last[1]} r="5" className="transport-card-map-marker transport-card-map-marker-end" />
                )}
              </svg>
              <div className="transport-card-map-footer">
                <span>{routeSource === 'live' ? 'Live GPS tracking active' : 'Route precomputed from shipment data'}</span>
              </div>
            </div>
          )}

            {timeline.length > 0 && (
              <div className="transport-card-timeline">
                {timeline.slice(-3).map((step) => (
                  <div key={`${step.id}-${step.created_at}`} className="transport-card-timeline-item">
                    <span>{step.status || 'Update'}</span>
                    <strong>{step.note || step.location || 'Status updated'}</strong>
                  </div>
                ))}
              </div>
            )}
          </div>
          {renderCardActions(actions, msg)}
        </>
      )
    }

    if (cardType === 'payment_card' || cardType === 'invoice_card') {
      const payment = cardData.latest_payment || cardData.payment || cardData
      const amount = payment.amount || cardData.amount || cardData.amount_due || 0
      const orderId = payment.razorpay_order_id || cardData.order_id || ''
      const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []
      const invoice = cardData.invoice || {}
      const isInvoiceCard = cardType === 'invoice_card'

      return (
        <>
          <div className="transport-card transport-card-payment">
            <div className="transport-card-header">
              <div>
                <p className="transport-card-kicker">{isInvoiceCard ? 'Invoice ready' : 'Secure payment'}</p>
                <h3>{getCardTitle(cardType, isInvoiceCard ? 'Invoice' : 'Payment')}</h3>
              </div>
              <span className="transport-card-chip transport-card-chip-success">{payment.payment_status || 'Pending'}</span>
            </div>

          <div className="transport-card-amount">{formatMoney(amount / 100 || amount)}</div>

          <div className="transport-card-grid transport-card-grid-compact">
            <div>
              <span>Booking</span>
              <strong>{cardData.booking_id ? `#${cardData.booking_id}` : '—'}</strong>
            </div>
            <div>
              <span>Shipment</span>
              <strong>{cardData.shipment_id ? `#${cardData.shipment_id}` : '—'}</strong>
            </div>
            <div>
              <span>Order ID</span>
              <strong>{orderId || 'Not created yet'}</strong>
            </div>
            <div>
              <span>Status</span>
              <strong>{payment.payment_status || cardData.status || 'created'}</strong>
            </div>
          </div>

            {isInvoiceCard && (
              <div className="transport-card-grid transport-card-grid-compact">
                <div>
                  <span>Invoice no</span>
                  <strong>{invoice.invoice_number || cardData.invoice_number || 'Pending'}</strong>
                </div>
                <div>
                  <span>Total</span>
                  <strong>{formatMoney(invoice.total_amount || invoice.amount || amount / 100 || amount)}</strong>
                </div>
              </div>
            )}

            {orderId && cardData.key_id ? (
              <div className="message-payment-cta transport-card-action-row">
                <button
                  type="button"
                  className="payment-action-button"
                  onClick={() => openPaymentCheckout(cardData)}
                  disabled={isPaying}
                >
                  {isPaying ? 'Opening payment...' : 'Pay now'}
                </button>
              </div>
            ) : null}
          </div>
          {renderCardActions(actions, msg)}
        </>
      )
    }

    if (cardType === 'booking_summary_card') {
      const booking = cardData.booking || cardData
      const assignment = cardData.driver || cardData.assignment || {}
      const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []

      return (
        <>
          <div className="transport-card transport-card-booking">
            <div className="transport-card-header">
              <div>
                <p className="transport-card-kicker">Shipment booking</p>
                <h3>{getCardTitle(cardType, 'Booking summary')}</h3>
              </div>
              <span className="transport-card-chip">{booking.truck_type || msg.meta?.lorryType || 'Quoted'}</span>
            </div>

          <div className="transport-card-grid">
            <div>
              <span>From</span>
              <strong>{booking.source_location || booking.source || '—'}</strong>
            </div>
            <div>
              <span>To</span>
              <strong>{booking.destination_location || booking.destination || '—'}</strong>
            </div>
            <div>
              <span>Distance</span>
              <strong>{formatMetric(booking.distance, ' km')}</strong>
            </div>
            <div>
              <span>Estimated fare</span>
              <strong>{formatMoney(booking.price)}</strong>
            </div>
          </div>

          <div className="transport-card-flow">
            <div className="transport-card-flow-header">
              <span>Advance token</span>
              <strong>{formatMoney(booking.token_amount)}</strong>
            </div>
            <div className="transport-card-flow-bar">
              <span style={{ width: '80%' }} />
            </div>
          </div>

            <div className="transport-card-grid transport-card-grid-compact">
              <div>
                <span>Assigned truck</span>
                <strong>{booking.lorry_number || assignment.truck_number || 'Pending'}</strong>
              </div>
              <div>
                <span>Driver</span>
                <strong>{assignment.driver_name || 'Pending assignment'}</strong>
              </div>
            </div>
          </div>
          {renderCardActions(actions, msg)}
        </>
      )
    }

    if (cardType === 'analytics_card') {
      const summary = cardData.summary || {}
      return (
        <div className="transport-card transport-card-analytics">
          <div className="transport-card-header">
            <div>
              <p className="transport-card-kicker">Operations snapshot</p>
              <h3>{getCardTitle(cardType, 'Analytics')}</h3>
            </div>
            <span className="transport-card-chip">{cardData.window_days ? `${cardData.window_days}d` : 'Live'}</span>
          </div>

          <div className="transport-card-metrics">
            <div><span>Total shipments</span><strong>{formatMetric(summary.total_shipments)}</strong></div>
            <div><span>Active shipments</span><strong>{formatMetric(summary.active_shipments)}</strong></div>
            <div><span>Delayed</span><strong>{formatMetric(summary.delayed_shipments)}</strong></div>
            <div><span>Revenue</span><strong>{formatMoney(summary.revenue_collected)}</strong></div>
          </div>
        </div>
      )
    }

    if (cardType === 'driver_assignment_card') {
      const driver = cardData.driver || cardData
      const assignedShipments = Array.isArray(cardData.assigned_shipments) ? cardData.assigned_shipments : []
      const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []

      return (
        <>
          <div className="transport-card transport-card-driver">
            <div className="transport-card-header">
              <div>
                <p className="transport-card-kicker">Driver dispatch</p>
                <h3>{getCardTitle(cardType, 'Driver assignment')}</h3>
              </div>
              <span className="transport-card-chip">{driver.status || 'Available'}</span>
            </div>

          <div className="transport-card-grid transport-card-grid-compact">
            <div><span>Name</span><strong>{driver.driver_name || 'Unassigned'}</strong></div>
            <div><span>Truck</span><strong>{driver.assigned_truck || driver.assigned_truck_type || '—'}</strong></div>
            <div><span>Rating</span><strong>{Number.isFinite(Number(driver.rating)) ? Number(driver.rating).toFixed(1) : '—'}</strong></div>
            <div><span>Experience</span><strong>{formatMetric(driver.experience_years, ' yrs')}</strong></div>
          </div>

            {assignedShipments.length > 0 && (
              <div className="transport-card-timeline">
                {assignedShipments.slice(0, 2).map((shipment) => (
                  <div key={shipment.id} className="transport-card-timeline-item">
                    <span>Shipment #{shipment.id}</span>
                    <strong>{shipment.pickup_location} → {shipment.drop_location}</strong>
                  </div>
                ))}
              </div>
            )}
          </div>
          {renderCardActions(actions, msg)}
        </>
      )
    }

    if (cardType === 'eta_status_card') {
      const etaHours = cardData.eta_hours ?? cardData.eta_minutes ?? cardData.estimated_eta
      const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []
      return (
        <>
          <div className="transport-card transport-card-eta">
            <div className="transport-card-header">
              <div>
                <p className="transport-card-kicker">Arrival status</p>
                <h3>{getCardTitle(cardType, 'ETA status')}</h3>
              </div>
              <span className="transport-card-chip">{cardData.shipment_status || cardData.status || 'In transit'}</span>
            </div>

          <div className={`transport-card-eta-value ${routeSource === 'live' ? 'is-live' : ''}`}>{formatRelativeEta(etaHours)}</div>

            <div className="transport-card-grid transport-card-grid-compact">
              <div><span>Distance</span><strong>{formatMetric(cardData.distance, ' km')}</strong></div>
              <div><span>Fare</span><strong>{formatMoney(cardData.estimated_fare)}</strong></div>
              <div><span>Truck</span><strong>{cardData.truck_type || '—'}</strong></div>
              <div><span>Route</span><strong>{cardData.pickup_location || cardData.source || '—'} → {cardData.drop_location || cardData.destination || '—'}</strong></div>
            </div>
          </div>
          {renderCardActions(actions, msg)}
        </>
      )
    }

        if (cardType === 'ai_progress_card') {
          const workflow = cardData.workflow || {}
          const actions = Array.isArray(cardData.actions) ? cardData.actions : Array.isArray(msg.meta?.actions) ? msg.meta.actions : []
          const steps = Array.isArray(cardData.steps) ? cardData.steps : Array.isArray(cardData.timeline) ? cardData.timeline : []
          const overall = Number(cardData.progress || (steps.length ? Math.round((steps.filter((s) => s.status === 'completed').length / steps.length) * 100) : 0))
          const isRunning = String(cardData.status || '').toLowerCase() === 'running' || overall < 100

          return (
            <>
              <div className="transport-card transport-card-ai-progress">
                <div className="transport-card-header">
                  <div>
                    <p className="transport-card-kicker">AI workflow</p>
                    <h3>{getCardTitle(cardType, workflow.name || 'Workflow progress')}</h3>
                  </div>
                  <span className={`transport-card-chip ${isRunning ? 'transport-card-chip-warning' : 'transport-card-chip-success'}`}>{isRunning ? 'Running' : 'Completed'}</span>
                </div>

                <div className="transport-card-flow">
                  <div className="transport-card-flow-header">
                    <span>Progress</span>
                    <strong>{overall}%</strong>
                  </div>
                  <div className="transport-card-flow-bar">
                    <span style={{ width: `${Math.min(100, Math.max(2, overall))}%` }} />
                  </div>
                </div>

                <div className="transport-card-timeline">
                  {steps.length === 0 && (
                    <div className="transport-card-loading">Running workflow…</div>
                  )}
                  {steps.map((step, idx) => (
                    <div key={`${idx}-${step.name || step.action || idx}`} className={`transport-card-timeline-item ${step.status || ''}`}>
                      <span>{step.name || step.action || `Step ${idx + 1}`}</span>
                      <strong>{step.note || (step.status === 'completed' ? 'Done' : step.status || 'Pending')}</strong>
                    </div>
                  ))}
                </div>

                {cardData.details && expandedMessages[msg.id] && (
                  <pre className="transport-card-details">{JSON.stringify(cardData.details, null, 2)}</pre>
                )}
              </div>
              {renderCardActions(actions, msg)}
            </>
          )
        }

    return null
  }

  const handleFareEstimateChange = (event) => {
    const { name, value } = event.target
    setFareEstimateForm((previous) => ({
      ...previous,
      [name]: value,
    }))
  }

  const handleFareEstimateSubmit = async (event) => {
    event.preventDefault()

    if (isEstimating) {
      return
    }

    const pickupLocation = fareEstimateForm.pickup_location.trim()
    const dropLocation = fareEstimateForm.drop_location.trim()
    const truckType = fareEstimateForm.truck_type.trim()
    const loadWeight = Number(fareEstimateForm.load_weight)

    if (!pickupLocation || !dropLocation || !truckType || !Number.isFinite(loadWeight)) {
      appendMessage('Please fill pickup, drop, weight, and truck type to estimate the fare.', 'bot')
      return
    }

    setIsEstimating(true)
    appendMessage(
      `Estimate fare: ${pickupLocation} to ${dropLocation}, ${loadWeight} tons, ${truckType}`,
      'user',
    )

    try {
      const response = await fetch(`${API_BASE_URL}/fare-estimate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          pickup_location: pickupLocation,
          drop_location: dropLocation,
          load_weight: loadWeight,
          truck_type: truckType,
        }),
      })

      const contentType = response.headers.get('content-type') || ''

      let data = {}
      if (contentType.includes('application/json')) {
        data = await response.json()
      } else {
        data = { message: await response.text() }
      }

      if (!response.ok) {
        throw new Error(data.message || 'Unable to estimate fare right now.')
      }

      appendMessage(data.reply || data.message || 'Fare estimate ready.', 'bot', { variant: 'summary' })
      setFareEstimateForm((previous) => ({
        ...previous,
        load_weight: '',
      }))
    } catch (error) {
      appendMessage(error instanceof Error ? error.message : 'Unable to estimate fare right now.', 'bot')
    } finally {
      setIsEstimating(false)
    }
  }

  const beginSendLock = () => {
    if (isSendingRef.current) {
      return false
    }

    isSendingRef.current = true
    setIsSending(true)
    return true
  }

  const endSendLock = () => {
    isSendingRef.current = false
    setIsSending(false)
  }

  const summarySource = chatSource || source || ''
  const summaryDestination = chatDestination || destination || ''
  const summaryWeight = pendingTons !== null ? `${pendingTons} tons` : expectingTons ? 'Awaiting load weight' : ''
  const summaryTyreType = pendingTons !== null ? getTruckTypeFromTons(Number(pendingTons)) || 'Pending' : ''
  const summaryDistance = Number.isFinite(distance) ? `${Number(distance)} km` : distanceMessage || 'Awaiting route estimate'
  const summaryPrice = Number.isFinite(price) ? `₹${Number(price)}` : 'Pending'

  const bookingSteps = [
    { label: 'Source', active: Boolean(summarySource) },
    { label: 'Destination', active: Boolean(summaryDestination) },
    { label: 'Weight', active: Boolean(summaryWeight && !summaryWeight.includes('Awaiting')) },
    { label: 'Quote', active: Number.isFinite(distance) || Number.isFinite(price) },
    { label: 'Confirmation', active: awaitingBookingDecision },
  ]

  const bookingProgress = Math.round((bookingSteps.filter((step) => step.active).length / bookingSteps.length) * 100)
  const showBookingSummary = Boolean(summarySource || summaryDestination || summaryWeight || Number.isFinite(distance) || Number.isFinite(price) || awaitingBookingDecision || expectingTons)

  // Calculate distance and price when both source and destination are available
  useEffect(() => {
    if (!chatSource?.trim() || !chatDestination?.trim()) {
      return
    }

    const calculateTransportValues = async () => {
      setCalculatingDistance(true)
      try {
        if (process.env.NODE_ENV !== 'production') {
          console.log(`📍 Calculating distance from "${chatSource}" to "${chatDestination}"...`)
        }
        const calculatedDistance = await calculateDistance(chatSource, chatDestination)
        if (process.env.NODE_ENV !== 'production') {
          console.log(`✓ Distance calculated: ${calculatedDistance} km`)
        }

        // only set distance here — do NOT calculate price until tons provided
        setDistance(calculatedDistance)
        setPrice(null)
      } catch (error) {
        console.error('❌ Error calculating distance/price:', error)
        setDistance(null)
        setPrice(null)
      } finally {
        setCalculatingDistance(false)
      }
    }

    calculateTransportValues()
  }, [chatSource, chatDestination])

  // When pendingTons is set and distance is available, compute price and send final payload
  useEffect(() => {
    const sendFinal = async () => {
      if (pendingTons === null) return
      if (calculatingDistance) return
      if (distance === null || !Number.isFinite(distance)) {
        // wait until distance is available
        return
      }

      const tonsVal = Number(pendingTons)
      const truckType = getTruckTypeFromTons(tonsVal)

      if (!truckType) {
        appendMessage('No lorry available for the provided tons.', 'bot')
        setPendingTons(null)
        setLoading(false)
        endSendLock()
        return
      }

      const finalPrice = calculatePrice(distance)

      // Build final payload and send to backend
      try {
        const userId = userIdRef.current || undefined
        const payload = {
          // send the destination as the message so backend records it as destination step
          message: chatDestination || '',
          source: chatSource || '',
          destination: chatDestination || '',
          tons: tonsVal,
          truck_type: truckType,
          distance: Number(distance),
          price: Number(finalPrice),
          ...(userId ? { user_id: userId } : {}),
        }

        if (process.env.NODE_ENV !== 'production') {
          console.log('📤 Final payload to backend:', payload)
        }

        const res = await fetch(`${API_BASE_URL}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })

        let data = {}
        const ct = res.headers.get('content-type') || ''
        if (ct.includes('application/json')) data = await res.json()

        // Prefer structured error or confirmation responses from backend
        if (data && typeof data.error === 'string') {
          appendMessage(data.error, 'bot', { variant: 'error' })
        } else if (data && data.status === 'confirmed') {
          const d = data.distance || distance
          const p = data.price || finalPrice
          const l = data.lorryType || ''
          appendMessage(`Distance: ${d} km\nEstimated Price: ₹${p}\nLorry Type: ${l}`, 'bot', { variant: 'summary', meta: data })
          // Reset state after successful booking
          setChatSource(null)
          setChatDestination(null)
          setDistance(null)
          setPrice(null)
          setExpectingTons(false)
          setPendingTons(null)
          // Append confirmation and wait for yes/no decision
          appendMessage('Booking confirmed! Do you want to book another transport?', 'bot', { meta: data })
          setAwaitingBookingDecision(true)
        } else {
          const reply = typeof data.reply === 'string' ? data.reply : ''
          appendMessage(reply || 'Received response from server.', 'bot', { meta: data })
        }
      } catch (err) {
        console.error('❌ Error sending final payload:', err)
        appendMessage('Error: could not complete booking. Try again later.', 'bot', { variant: 'error' })
      } finally {
        setPendingTons(null)
        setLoading(false)
        endSendLock()
      }
    }

    sendFinal()
  }, [pendingTons, calculatingDistance, distance])

  const sendPendingMessage = async () => {
    if (!pendingMessage) return

    try {
      const { userInput } = pendingMessage
      const distanceVal = distance !== null && Number.isFinite(distance) ? Number(distance) : null
      const priceVal = price !== null && Number.isFinite(price) ? Number(price) : null

      if (process.env.NODE_ENV !== 'production') {
        console.log('📤 Sending pending destination message')
        console.log('   distance:', distanceVal, 'price:', priceVal)
      }

      const response = await sendMessage(userInput, distanceVal, priceVal)
      appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot', { meta: response })
    } catch (error) {
      console.error('❌ Failed to send pending message:', error)
      appendMessage('Error: Could not reach the chat service. Please try again.', 'bot', { variant: 'error' })
    } finally {
      setLoading(false)
      setPendingMessage(null)
    }
  }

  const handleSendMessage = async ({ messageText = null, echoUserMessage = true } = {}) => {
    const userInput = String(messageText ?? input).trim()
    if (!userInput || loading || isSendingRef.current) return

    if (!beginSendLock()) return

    if (echoUserMessage) {
      appendMessage(userInput, 'user')
    }
    if (messageText === null) {
      setInput('')
    }
    setLoading(true)
    setSendError('')
    let keepSendLock = false

    try {
      // Handle booking decision (yes/no for another transport)
      if (awaitingBookingDecision) {
        const userSaysYes = userInput.toLowerCase().includes('yes')
        const userSaysNo = userInput.toLowerCase().includes('no')
        const response = await sendMessage(userInput, null, null)

        if (userSaysYes) {
          setAwaitingBookingDecision(false)
          setIsRestartingAfterBooking(true)
          if (response?.reply && !response.reply.includes('Hi 👋')) {
            appendMessage(response.reply, 'bot', { meta: response })
          } else if (response?.reply && response.reply.includes('Hi 👋')) {
            setIsRestartingAfterBooking(false)
          }
          return
        }

        if (userSaysNo) {
          setAwaitingBookingDecision(false)
          appendMessage(response?.reply || 'Thank you for using our service! Have a great day.', 'bot', { meta: response })
          return
        }

        appendMessage(response?.reply || 'Please reply with "yes" to book another transport or "no" to exit.', 'bot', { meta: response })
        return
      }

      const lastBotMessage = messages.filter((m) => m.sender === 'bot').pop()?.text || ''

      if (lastBotMessage.includes('Please enter your source location')) {
        setChatSource(userInput)
        const response = await sendMessage(userInput, null, null)
        appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot', { meta: response })
      } else if (lastBotMessage.includes('Enter your destination')) {
        setChatDestination(userInput)
        const response = await sendMessage(userInput, null, null)
        appendMessage(response?.reply || 'Enter tons', 'bot', { meta: response })
        setExpectingTons(true)
      } else if (expectingTons) {
        const tonsVal = Number(userInput)
        const truckType = getTruckTypeFromTons(tonsVal)
        if (!Number.isFinite(tonsVal)) {
          const response = await sendMessage(userInput, null, null)
          appendMessage(response?.reply || 'Please enter a valid tons number (e.g., 22)', 'bot', { meta: response })
        } else if (!truckType) {
          const response = await sendMessage(userInput, null, null)
          appendMessage(response?.reply || 'No lorry available for the provided tons.', 'bot', { meta: response })
        } else {
          setPendingTons(tonsVal)
          setExpectingTons(false)
          keepSendLock = true
        }
      } else {
        // Use streaming endpoint when available to get progressive AI reply
        try {
          const streamResp = await fetch(`${API_BASE_URL}/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: userInput, user_id: userIdRef.current || null, session_id: '' }),
          })
          if (!streamResp.ok) {
            // fallback to synchronous send
            const response = await sendMessage(userInput, null, null)
            const isGreeting = response?.reply?.includes('Hi 👋')
            if (isGreeting && isRestartingAfterBooking) {
              setIsRestartingAfterBooking(false)
            } else if (isGreeting && isFirstMessage) {
              setIsFirstMessage(false)
            }
            appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot', { meta: response })
          } else {
            // Streaming will drive the assistant replies via socket events
          }
        } catch (err) {
          // network fallback
          const response = await sendMessage(userInput, null, null)
          appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot', { meta: response })
        }
        // mark as handled
        setLoading(false)
        setPendingMessage(null)
        return

        if (isGreeting && isRestartingAfterBooking) {
          setIsRestartingAfterBooking(false)
        } else if (isGreeting && isFirstMessage) {
          appendMessage(response.reply, 'bot', { meta: response })
          setIsFirstMessage(false)
        } else {
          appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot', { meta: response })
        }
      }
    } catch (error) {
      console.error('❌ Failed to send message:', error)
      const errorMessage = error instanceof Error ? error.message : 'Error: Could not reach the chat service. Please try again.'
      setSendError(errorMessage)
      setRetryDraft(userInput)
      appendMessage(errorMessage, 'bot', { variant: 'error' })
      setPendingMessage(null)
    } finally {
      if (!keepSendLock) {
        setLoading(false)
        endSendLock()
      }
    }
  }

  const handleRoleQuickAction = (prompt) => {
    if (!prompt || loading || isSendingRef.current || isEstimating) {
      return
    }

    handleSendMessage({ messageText: prompt, echoUserMessage: true })
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (loading || isSending || isEstimating) {
        return
      }
      handleSendMessage()
    }
  }

  const handleAssistantDockToggle = () => {
    setAssistantDockOpen((prev) => !prev)
  }

  return (
    <div className="chat-page">
      <div className="chat-container">
        <header className="chat-header">
          <div className="chat-brand">
            <div className="chat-brand-mark" aria-hidden="true">
              🚛
            </div>
            <div>
              <p className="chat-eyebrow">Logistics Control Tower</p>
              <h2>SKDLS Transport Assistant</h2>
            </div>
          </div>

          <div className="chat-header-status">
            <span className="status-pill status-pill-online">
              <span className="status-dot" />
              Online
            </span>
            <span className="status-pill">GPS Live</span>
            <span className="status-pill">DB Connected</span>
          </div>
        </header>

        <div className="chat-workspace">
          {showBookingSummary && (
            <aside className="booking-summary-card" aria-label="Shipment summary">
              <div className="summary-card-header">
                <div>
                  <p className="summary-kicker">Current shipment</p>
                  <h3>Shipment Summary</h3>
                </div>
                <div className="summary-route-chip">Live route</div>
              </div>

              <div className="summary-grid">
                <div className="summary-item">
                  <span className="summary-label">Source</span>
                  <strong>{summarySource || 'Awaiting source'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Destination</span>
                  <strong>{summaryDestination || 'Awaiting destination'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Weight</span>
                  <strong>{summaryWeight || 'Awaiting weight'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Tyre type</span>
                  <strong>{summaryTyreType || 'Pending'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Distance</span>
                  <strong>{summaryDistance}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Estimate</span>
                  <strong>{summaryPrice}</strong>
                </div>
              </div>

              <div className="summary-progress-block">
                <div className="summary-progress-header">
                  <span>Shipment progress</span>
                  <strong>{bookingProgress}%</strong>
                </div>
                <div className="summary-progress-bar" aria-hidden="true">
                  <span style={{ width: `${bookingProgress}%` }} />
                </div>
                <div className="summary-steps" aria-label="Booking steps">
                  {bookingSteps.map((step) => (
                    <span key={step.label} className={`summary-step ${step.active ? 'is-active' : ''}`}>
                      {step.label}
                    </span>
                  ))}
                </div>
              </div>
            </aside>
          )}

          <section className={`fare-estimator-card ${isEstimating ? 'is-loading' : ''}`} aria-label="AI fare estimator">
            <div className="fare-estimator-header">
              <div>
                <p className="fare-estimator-kicker">AI fare estimation</p>
                <h3>Get distance, fare, and ETA instantly</h3>
              </div>
              <span className="fare-estimator-badge">OSM + OSRM</span>
            </div>

            <form className="fare-estimator-form" onSubmit={handleFareEstimateSubmit}>
              <label>
                <span>Pickup</span>
                <input
                  name="pickup_location"
                  type="text"
                  value={fareEstimateForm.pickup_location}
                  onChange={handleFareEstimateChange}
                  placeholder="Hyderabad"
                />
              </label>
              <label>
                <span>Drop</span>
                <input
                  name="drop_location"
                  type="text"
                  value={fareEstimateForm.drop_location}
                  onChange={handleFareEstimateChange}
                  placeholder="Bangalore"
                />
              </label>
              <label>
                <span>Weight (tons)</span>
                <input
                  name="load_weight"
                  type="number"
                  min="0.1"
                  step="0.1"
                  value={fareEstimateForm.load_weight}
                  onChange={handleFareEstimateChange}
                  placeholder="4"
                />
              </label>
              <label>
                <span>Truck type</span>
                <select name="truck_type" value={fareEstimateForm.truck_type} onChange={handleFareEstimateChange}>
                  <option value="12 tyre">12 tyre</option>
                  <option value="14 tyre">14 tyre</option>
                  <option value="16 tyre">16 tyre</option>
                </select>
              </label>

              <button type="submit" className="fare-estimator-button" disabled={isEstimating}>
                {isEstimating ? (
                  <span className="button-loading">
                    <span className="spinner" />
                    Estimating...
                  </span>
                ) : (
                  'Estimate fare'
                )}
              </button>
            </form>
          </section>

          <section className="realtime-feed-card" aria-label="Realtime shipment activity">
            <div className="realtime-feed-header">
              <div>
                <p className="realtime-feed-kicker">Realtime operations</p>
                <h3>Live shipment activity feed</h3>
              </div>
              <span className="realtime-feed-badge">Socket.IO live</span>
            </div>

            <div className="realtime-feed-grid">
              <div className="realtime-feed-panel">
                <strong>Notifications</strong>
                <div className="realtime-feed-list">
                  {realtimeFeed.slice(0, 4).map((event) => (
                    <article key={event.id} className={`realtime-feed-item realtime-feed-item-${String(event.level || 'info')}`}>
                      <span>{event.title || 'Operational update'}</span>
                      <p>{event.message || 'Live update received.'}</p>
                    </article>
                  ))}
                  {realtimeFeed.length === 0 && <p className="realtime-feed-empty">Waiting for live shipment events...</p>}
                </div>
              </div>

              <div className="realtime-feed-panel">
                <strong>Recent activity</strong>
                <div className="realtime-feed-list realtime-feed-list-compact">
                  {realtimeFeed.slice(0, 6).map((event) => (
                    <article key={`${event.id}-compact`} className="realtime-feed-item realtime-feed-item-compact">
                      <span>{event.type || 'event'}</span>
                      <p>{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                    </article>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="chat-thread">
            <div className="chat-messages">
              {(welcomeLoading || isEstimating) && (
                <div className="welcome-loading">
                  <span className="typing-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  {isEstimating ? 'Calculating route, fare, and ETA...' : 'Connecting to transport assistant...'}
                </div>
              )}
              {/* Welcome handled by backend; placeholder removed to avoid duplicate messages */}

              {messages.map((msg) => (
                <div key={msg.id} className={`message message-${msg.sender}`}>
                  {msg.sender === 'bot' && getMessageCardType(msg) ? (
                    <div className={`message-bubble message-card ${msg.variant === 'summary' ? 'message-summary' : ''} ${msg.is_streaming ? 'is-streaming' : ''}`}>
                      {renderChatCard(msg)}
                    </div>
                  ) : (
                    <div className={`message-bubble ${msg.variant === 'summary' ? 'message-summary' : ''} ${msg.variant === 'error' ? 'message-error' : ''} ${msg.is_streaming ? 'is-streaming' : ''}`}>
                      {msg.text}
                    </div>
                  )}
                  {msg.sender === 'bot' && msg.meta && !getMessageCardType(msg) && (
                    <div className="message-meta-row">
                      <span className="message-meta-chip">{formatIntentLabel(msg.meta.intent)}</span>
                      {msg.meta.workflow ? <span className="message-meta-chip message-meta-chip-soft">{formatWorkflowLabel(msg.meta.workflow)}</span> : null}
                      {typeof msg.meta.confidence === 'number' && msg.meta.confidence > 0 ? (
                        <span className="message-meta-chip message-meta-chip-muted">{Math.round(msg.meta.confidence * 100)}%</span>
                      ) : null}
                    </div>
                  )}
                  {msg.sender === 'bot' && Array.isArray(msg.meta?.suggestions) && msg.meta.suggestions.length > 0 && (
                    <div className="message-suggestions">
                      {msg.meta.suggestions.map((suggestion) => (
                        <button
                          key={suggestion}
                          type="button"
                          className="message-suggestion-chip"
                          onClick={() => setInput(suggestion)}
                        >
                          {suggestion}
                        </button>
                      ))}
                    </div>
                  )}
                  {msg.sender === 'bot' && !getMessageCardType(msg) && msg.meta?.intent === 'MAKE_PAYMENT' && msg.meta?.data?.order_id && (
                    <div className="message-payment-cta">
                      <button
                        type="button"
                        className="payment-action-button"
                        onClick={() => openPaymentCheckout(msg.meta.data)}
                        disabled={isPaying}
                      >
                        {isPaying ? 'Opening payment...' : `Pay ₹${Math.round(Number(msg.meta.data.amount || 0) / 100)}`}
                      </button>
                    </div>
                  )}
                </div>
              ))}

              {(loading || isSending || isEstimating) && (
                <div className="message message-bot">
                  <div className="message-bubble loading-indicator">
                    <span className="typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                    {isEstimating ? 'Estimating fare...' : 'Typing...'}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {sendError && (
              <div className="chat-recovery-banner" role="status" aria-live="polite">
                <div>
                  <strong>Message not delivered</strong>
                  <p>{sendError}</p>
                </div>
                <div className="chat-recovery-actions">
                  <button
                    type="button"
                    className="chat-recovery-button chat-recovery-button-primary"
                    onClick={() => handleSendMessage({ messageText: retryDraft, echoUserMessage: false })}
                    disabled={!retryDraft || loading || isSending || isEstimating}
                  >
                    Retry
                  </button>
                  <button
                    type="button"
                    className="chat-recovery-button"
                    onClick={() => {
                      setSendError('')
                      setRetryDraft('')
                    }}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}

            <div className="role-quick-actions" aria-label="Role-specific quick actions">
              <div className="role-quick-actions-header">
                <strong>{role === 'admin' ? 'Admin shortcuts' : role === 'driver' ? 'Driver shortcuts' : 'Customer shortcuts'}</strong>
                <span>{roleSuggestionText}</span>
              </div>
              <div className="role-quick-actions-grid">
                {roleQuickActions.map((action) => (
                  <button
                    key={action.label}
                    type="button"
                    className="role-quick-action"
                    onClick={() => handleRoleQuickAction(action.prompt)}
                    disabled={loading || isSending || isEstimating}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="chat-input-area">
              <div className="input-shell">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Type your next transport request..."
                  disabled={loading || isSending || isEstimating}
                  rows="3"
                />
                <div className="input-hint-row">
                  <span>One message at a time keeps booking steps accurate.</span>
                  <span>{loading || isSending || isEstimating ? 'Assistant processing' : 'Ready'}</span>
                </div>
              </div>
              <button onClick={handleSendMessage} disabled={loading || isSending || isEstimating || !input.trim()} className="send-button">
                {loading || isSending || isEstimating ? (
                  <span className="button-loading">
                    <span className="spinner" />
                    Sending...
                  </span>
                ) : (
                  <span className="button-content">
                    <span className="send-icon" aria-hidden="true">
                      ↑
                    </span>
                    Send
                  </span>
                )}
              </button>
            </div>
          </section>
        </div>
      </div>

      <button type="button" className={`assistant-fab ${assistantDockOpen ? 'is-open' : ''}`} onClick={handleAssistantDockToggle} aria-label="Toggle assistant dock" aria-pressed={assistantDockOpen}>
        <span className="assistant-fab-icon" aria-hidden="true">
          🚚
        </span>
      </button>

      <div className={`assistant-dock ${assistantDockOpen ? 'is-open' : ''}`} aria-hidden={!assistantDockOpen}>
        <div className="assistant-dock-header">
          <strong>Assistant panel</strong>
          <span>Live</span>
        </div>
        <p>Tracking shipment flow, GPS updates, and transport replies in a single workspace.</p>
      </div>
    </div>
  )
}
