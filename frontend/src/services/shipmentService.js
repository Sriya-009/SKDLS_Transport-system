import { requestJson } from './http'

const normalizeText = (value) => String(value ?? '').trim()

const normalizeShipment = (data = {}) => ({
  id: data.id ?? data.shipment_id ?? null,
  user_id: normalizeText(data.user_id ?? data.userId),
  pickup_location: normalizeText(data.pickup_location ?? data.pickupLocation),
  drop_location: normalizeText(data.drop_location ?? data.dropLocation),
  cargo_type: normalizeText(data.cargo_type ?? data.cargoType),
  truck_type: normalizeText(data.truck_type ?? data.truckType),
  weight: data.weight ?? null,
  distance_km: data.distance_km ?? data.distanceKm ?? null,
  estimated_price: data.estimated_price ?? data.estimatedPrice ?? null,
  payment_status: normalizeText(data.payment_status ?? data.paymentStatus),
  shipment_status: normalizeText(data.shipment_status ?? data.shipmentStatus),
  assigned_driver_id: data.assigned_driver_id ?? data.assignedDriverId ?? null,
  created_at: normalizeText(data.created_at ?? data.createdAt),
  booking_reference: normalizeText(data.booking_reference ?? data.reference ?? data.shipment_reference),
  route_coordinates: Array.isArray(data.route_coordinates) ? data.route_coordinates : [],
  eta_hours: data.eta_hours ?? data.etaHours ?? null,
  assigned_driver: data.assigned_driver && typeof data.assigned_driver === 'object' ? data.assigned_driver : null,
})

const normalizeTimelineEntry = (entry = {}) => ({
  id: entry.id ?? null,
  shipment_id: entry.shipment_id ?? entry.shipmentId ?? null,
  status: normalizeText(entry.status),
  note: normalizeText(entry.note),
  location: normalizeText(entry.location),
  actor_role: normalizeText(entry.actor_role ?? entry.actorRole),
  metadata: entry.metadata && typeof entry.metadata === 'object' ? entry.metadata : {},
  created_at: normalizeText(entry.created_at ?? entry.createdAt),
})

const normalizeEvent = (event = {}) => ({
  id: event.id ?? null,
  shipment_id: event.shipment_id ?? event.shipmentId ?? null,
  event_type: normalizeText(event.event_type ?? event.eventType),
  title: normalizeText(event.title),
  message: normalizeText(event.message),
  severity: normalizeText(event.severity ?? 'info'),
  source: normalizeText(event.source),
  metadata: event.metadata && typeof event.metadata === 'object' ? event.metadata : {},
  created_at: normalizeText(event.created_at ?? event.createdAt),
})

const normalizeActivityItem = (item = {}) => ({
  ...normalizeEvent(item),
  shipment_reference: normalizeText(item.shipment_reference ?? item.shipmentReference),
  driver_reference: normalizeText(item.driver_reference ?? item.driverReference),
  route_summary: normalizeText(item.route_summary ?? item.routeSummary),
  eta_phrase: normalizeText(item.eta_phrase ?? item.etaPhrase),
  shipment_status: normalizeText(item.shipment_status ?? item.shipmentStatus),
  payment_status: normalizeText(item.payment_status ?? item.paymentStatus),
  timestamp: normalizeText(item.timestamp ?? item.created_at ?? item.createdAt),
  kind: normalizeText(item.kind),
  status_badge: normalizeText(item.status_badge ?? item.statusBadge),
  actions: Array.isArray(item.actions) ? item.actions : [],
  shipment: item.shipment && typeof item.shipment === 'object' ? normalizeShipment(item.shipment) : null,
  driver: item.driver && typeof item.driver === 'object' ? item.driver : null,
})

const normalizePayment = (payment = {}) => ({
  id: payment.id ?? null,
  booking_id: payment.booking_id ?? payment.bookingId ?? null,
  shipment_id: payment.shipment_id ?? payment.shipmentId ?? null,
  razorpay_order_id: normalizeText(payment.razorpay_order_id ?? payment.razorpayOrderId),
  razorpay_payment_id: normalizeText(payment.razorpay_payment_id ?? payment.razorpayPaymentId),
  amount: payment.amount ?? null,
  payment_status: normalizeText(payment.payment_status ?? payment.paymentStatus),
  payment_type: normalizeText(payment.payment_type ?? payment.paymentType),
  created_at: normalizeText(payment.created_at ?? payment.createdAt),
})

const normalizeRouteSuggestion = (item = {}) => ({
  id: item.id ?? null,
  kind: normalizeText(item.kind),
  label: normalizeText(item.label ?? item.route_label ?? item.value),
  value: normalizeText(item.value ?? item.label ?? item.route_label),
  route_key: normalizeText(item.route_key),
  pickup_location: normalizeText(item.pickup_location ?? item.pickupLocation),
  drop_location: normalizeText(item.drop_location ?? item.dropLocation),
  truck_type: normalizeText(item.truck_type ?? item.truckType ?? item.suggested_truck_type),
  estimated_price: item.estimated_price ?? null,
  eta_hours: item.eta_hours ?? item.etaHours ?? null,
  count: item.count ?? null,
  source: normalizeText(item.source),
  last_seen: normalizeText(item.last_seen ?? item.lastSeen),
})

function buildQueryString(params = {}) {
  const searchParams = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') {
      continue
    }

    searchParams.set(key, String(value))
  }

  return searchParams.toString()
}

export async function fetchShipmentEstimate(payload) {
  return requestJson('/fare-estimate', {
    method: 'POST',
    body: payload,
  })
}

export async function recommendVehicle(payload) {
  return requestJson('/vehicles/recommendation', {
    method: 'POST',
    body: payload,
  })
}

export async function createShipment(payload) {
  const response = await requestJson('/api/shipments', {
    method: 'POST',
    body: payload,
  })

  return {
    ...response,
    shipment: normalizeShipment(response.shipment || response.data || response),
    route_coordinates: Array.isArray(response.route_coordinates) ? response.route_coordinates : [],
    assigned_driver: response.assigned_driver ? response.assigned_driver : null,
  }
}

export async function fetchMyShipments() {
  const response = await requestJson('/api/shipments/my')

  return {
    ...response,
    shipments: Array.isArray(response.shipments) ? response.shipments.map(normalizeShipment) : [],
  }
}

export async function fetchShipmentById(id) {
  const response = await requestJson(`/api/shipments/${encodeURIComponent(id)}`)

  return {
    ...response,
    shipment: normalizeShipment(response.shipment || response.data || response),
  }
}

export async function fetchShipmentTimeline(shipmentId) {
  const response = await requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/timeline`)

  return {
    ...response,
    shipment: normalizeShipment(response.shipment || response.data || response.shipment),
    timeline: Array.isArray(response.timeline) ? response.timeline.map(normalizeTimelineEntry) : [],
  }
}

export async function fetchShipmentEvents(shipmentId) {
  const response = await requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/events`)

  return {
    ...response,
    shipment: normalizeShipment(response.shipment || response.data || response.shipment),
    events: Array.isArray(response.events) ? response.events.map(normalizeEvent) : [],
  }
}

export async function fetchShipmentActivityFeed(params = {}) {
  const queryString = buildQueryString(params)
  const response = await requestJson(`/api/shipments/activity-feed${queryString ? `?${queryString}` : ''}`)

  return {
    ...response,
    feed: Array.isArray(response.feed) ? response.feed.map(normalizeActivityItem) : [],
    active_shipments: Array.isArray(response.active_shipments) ? response.active_shipments.map(normalizeActivityItem) : [],
    recent_shipments: Array.isArray(response.recent_shipments) ? response.recent_shipments.map(normalizeActivityItem) : [],
    summary: response.summary && typeof response.summary === 'object' ? response.summary : {},
  }
}

export async function fetchUserPreferences() {
  const response = await requestJson('/api/users/preferences')
  return {
    ...response,
    preferences: response.preferences && typeof response.preferences === 'object' ? response.preferences : {},
  }
}

export async function saveUserPreferences(prefs = {}) {
  return requestJson('/api/users/preferences', {
    method: 'PUT',
    body: prefs,
  })
}

export async function deleteUserPreferences() {
  return requestJson('/api/users/preferences', {
    method: 'DELETE',
  })
}

export async function logUserSearch(query, metadata = {}) {
  return requestJson('/api/users/searches', {
    method: 'POST',
    body: { query, metadata },
  })
}

export async function fetchUserSearches(params = {}) {
  const qs = buildQueryString(params)
  const response = await requestJson(`/api/users/searches${qs ? `?${qs}` : ''}`)
  return {
    ...response,
    searches: Array.isArray(response.searches) ? response.searches : [],
  }
}

export async function fetchUserRecommendations(params = {}) {
  const qs = buildQueryString(params)
  const response = await requestJson(`/api/users/recommendations${qs ? `?${qs}` : ''}`)
  return {
    ...response,
    recommendations: Array.isArray(response.recommendations) ? response.recommendations : [],
  }
}

export async function fetchShipmentInvoice(shipmentId) {
  return requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/invoice`)
}

export async function fetchShipmentPayments(shipmentId) {
  const params = new URLSearchParams()
  params.set('shipment_id', shipmentId)

  const response = await requestJson(`/payments/history?${params.toString()}`)

  return {
    ...response,
    payments: Array.isArray(response.payments) ? response.payments.map(normalizePayment) : [],
  }
}

export async function createShipmentPaymentOrder(shipmentId, paymentType = 'advance') {
  return requestJson('/payments/create-order', {
    method: 'POST',
    body: {
      shipment_id: shipmentId,
      payment_type: paymentType,
    },
  })
}

export async function fetchRouteSuggestions(params = {}) {
  const queryString = buildQueryString(params)
  const response = await requestJson(`/api/search/suggestions${queryString ? `?${queryString}` : ''}`)

  return {
    ...response,
    suggestions: Array.isArray(response.suggestions) ? response.suggestions.map(normalizeRouteSuggestion) : [],
    pickup_suggestions: Array.isArray(response.pickup_suggestions) ? response.pickup_suggestions.map(normalizeRouteSuggestion) : [],
    drop_suggestions: Array.isArray(response.drop_suggestions) ? response.drop_suggestions.map(normalizeRouteSuggestion) : [],
    recent_searches: Array.isArray(response.recent_searches) ? response.recent_searches.map(normalizeRouteSuggestion) : [],
    popular_routes: Array.isArray(response.popular_routes) ? response.popular_routes.map(normalizeRouteSuggestion) : [],
    frequently_booked_routes: Array.isArray(response.frequently_booked_routes) ? response.frequently_booked_routes.map(normalizeRouteSuggestion) : [],
    personalized_suggestions: Array.isArray(response.personalized_suggestions) ? response.personalized_suggestions.map(normalizeRouteSuggestion) : [],
    live_estimate: response.live_estimate && typeof response.live_estimate === 'object' ? response.live_estimate : null,
  }
}

export async function fetchRouteIntelligence(params = {}) {
  const queryString = buildQueryString(params)
  const response = await requestJson(`/api/search/routes${queryString ? `?${queryString}` : ''}`)

  return {
    ...response,
    routes: Array.isArray(response.routes) ? response.routes.map(normalizeRouteSuggestion) : [],
    pickup_suggestions: Array.isArray(response.pickup_suggestions) ? response.pickup_suggestions.map(normalizeRouteSuggestion) : [],
    drop_suggestions: Array.isArray(response.drop_suggestions) ? response.drop_suggestions.map(normalizeRouteSuggestion) : [],
    recent_searches: Array.isArray(response.recent_searches) ? response.recent_searches.map(normalizeRouteSuggestion) : [],
    popular_routes: Array.isArray(response.popular_routes) ? response.popular_routes.map(normalizeRouteSuggestion) : [],
    frequently_booked_routes: Array.isArray(response.frequently_booked_routes) ? response.frequently_booked_routes.map(normalizeRouteSuggestion) : [],
    personalized_suggestions: Array.isArray(response.personalized_suggestions) ? response.personalized_suggestions.map(normalizeRouteSuggestion) : [],
    live_estimate: response.live_estimate && typeof response.live_estimate === 'object' ? response.live_estimate : null,
  }
}

export async function verifyShipmentPayment(payload) {
  return requestJson('/payments/verify', {
    method: 'POST',
    body: payload,
  })
}

export async function fetchAdminShipments() {
  const response = await requestJson('/api/admin/shipments')

  return {
    ...response,
    shipments: Array.isArray(response.shipments) ? response.shipments.map(normalizeShipment) : [],
  }
}

export async function updateAdminShipmentStatus(shipmentId, payload) {
  return requestJson(`/api/admin/shipments/${encodeURIComponent(shipmentId)}/status`, {
    method: 'PUT',
    body: payload,
  })
}

export async function fetchShipmentHistory({ phone, limit = 20 } = {}) {
  const params = new URLSearchParams()
  if (phone) {
    params.set('phone', phone)
  }
  if (limit) {
    params.set('limit', String(limit))
  }

  const response = await requestJson(`/api/shipments/my${params.toString() ? `?${params.toString()}` : ''}`)

  return {
    ...response,
    shipments: Array.isArray(response.shipments) ? response.shipments.map(normalizeShipment) : [],
  }
}
