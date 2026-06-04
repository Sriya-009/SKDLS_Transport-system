import {
  createShipment,
  createShipmentPaymentOrder,
  fetchShipmentById,
  fetchShipmentHistory,
  fetchShipmentInvoice,
  fetchShipmentPayments,
  fetchShipmentTimeline,
  fetchMyShipments,
  recommendVehicle as recommendShipmentVehicle,
  updateAdminShipmentStatus,
  verifyShipmentPayment,
} from './shipmentService'

const normalizeText = (value) => String(value ?? '').trim()

const normalizeBooking = (data = {}, fallback = {}) => ({
  id: data.id ?? data.shipment_id ?? data.booking_id ?? null,
  booking_reference: normalizeText(data.booking_reference ?? data.reference ?? data.shipment_reference),
  customer_name: normalizeText(fallback.customer_name ?? fallback.customerName),
  phone: normalizeText(fallback.phone),
  pickup_location: normalizeText(data.pickup_location ?? data.pickupLocation ?? fallback.pickup_location ?? fallback.pickupLocation),
  drop_location: normalizeText(data.drop_location ?? data.dropLocation ?? fallback.drop_location ?? fallback.dropLocation),
  truck_type: normalizeText(data.truck_type ?? data.truckType ?? fallback.truck_type ?? fallback.truckType),
  load_weight: data.weight ?? data.load_weight ?? data.loadWeight ?? fallback.load_weight ?? fallback.loadWeight ?? null,
  price: data.estimated_price ?? data.price ?? null,
  booking_status: normalizeText(data.shipment_status ?? data.booking_status ?? data.bookingStatus),
  created_at: normalizeText(data.created_at ?? data.createdAt),
  whatsapp_notifications_enabled: Boolean(fallback.whatsapp_notifications_enabled ?? true),
  whatsapp_booking_confirmation: Boolean(fallback.whatsapp_booking_confirmation ?? true),
  whatsapp_payment_confirmation: Boolean(fallback.whatsapp_payment_confirmation ?? true),
  whatsapp_live_location: Boolean(fallback.whatsapp_live_location ?? true),
  whatsapp_delivery_updates: Boolean(fallback.whatsapp_delivery_updates ?? true),
})

const normalizeTimelineEntry = (entry = {}) => ({
  id: entry.id ?? null,
  booking_id: entry.booking_id ?? entry.bookingId ?? null,
  status: normalizeText(entry.status),
  note: normalizeText(entry.note),
  location: normalizeText(entry.location),
  actor_role: normalizeText(entry.actor_role ?? entry.actorRole),
  metadata: entry.metadata && typeof entry.metadata === 'object' ? entry.metadata : {},
  created_at: normalizeText(entry.created_at ?? entry.createdAt),
})

export async function createBooking(payload) {
  const response = await createShipment(payload)
  const booking = normalizeBooking(response.shipment || response.data || response, payload)

  return {
    ...response,
    booking,
    booking_id: response.booking_id ?? booking.id,
  }
}

export async function createPaymentOrder(bookingId) {
  return createShipmentPaymentOrder(bookingId, 'advance')
}

export async function createPaymentOrderForType(bookingId, paymentType = 'advance') {
  return createShipmentPaymentOrder(bookingId, paymentType)
}

export async function verifyPaymentSignature(payload) {
  return verifyShipmentPayment(payload)
}

export async function fetchPaymentHistory(bookingId) {
  const data = await fetchShipmentPayments(bookingId)

  return {
    ...data,
    payments: Array.isArray(data.payments) ? data.payments : [],
  }
}

export async function fetchBookingInvoice(bookingId) {
  return fetchShipmentInvoice(bookingId)
}

export async function fetchBookings() {
  const data = await fetchMyShipments()

  return {
    ...data,
    bookings: Array.isArray(data.shipments) ? data.shipments.map((shipment) => normalizeBooking(shipment)) : [],
  }
}

export async function fetchBookingById(id) {
  const data = await fetchShipmentById(id)

  return {
    ...data,
    booking: normalizeBooking(data.shipment || data.data || data),
  }
}

export async function fetchBookingHistory({ phone, limit = 20 } = {}) {
  const data = await fetchShipmentHistory({ phone, limit })

  return {
    ...data,
    bookings: Array.isArray(data.shipments) ? data.shipments.map((shipment) => normalizeBooking(shipment)) : [],
  }
}

export async function fetchBookingTimeline(bookingId) {
  const data = await fetchShipmentTimeline(bookingId)

  return {
    ...data,
    booking: normalizeBooking(data.shipment || data.data || data.booking),
    timeline: Array.isArray(data.timeline) ? data.timeline.map(normalizeTimelineEntry) : [],
  }
}

export async function recommendVehicle(payload) {
  return recommendShipmentVehicle(payload)
}

export async function updateBookingStatus(bookingId, payload) {
  return updateAdminShipmentStatus(bookingId, payload)
}