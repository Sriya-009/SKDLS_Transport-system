import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { MapContainer, Polyline, TileLayer } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { fetchShipmentById, fetchShipmentPayments, fetchShipmentTimeline } from '../services/shipmentService'
import './Booking.css'

function formatCurrency(value) {
  const numeric = Number(value || 0)
  if (!Number.isFinite(numeric)) {
    return 'Pending'
  }

  return `₹${numeric.toLocaleString('en-IN')}`
}

export default function BookingConfirmation() {
  const { bookingId } = useParams()
  const location = useLocation()
  const [booking, setBooking] = useState(location.state?.booking || null)
  const [routeCoordinates] = useState(Array.isArray(location.state?.routeCoordinates) ? location.state.routeCoordinates : [])
  const [timeline, setTimeline] = useState([])
  const [payments, setPayments] = useState([])
  const [loading, setLoading] = useState(!location.state?.booking)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [paymentLoading, setPaymentLoading] = useState(false)
  const [error, setError] = useState('')

  const bookingReference = useMemo(
    () => booking?.booking_reference || (bookingId ? `Shipment #${bookingId}` : 'Shipment confirmation'),
    [booking?.booking_reference, bookingId],
  )

  const downloadInvoice = () => {
    if (!bookingId) {
      return
    }

    window.open(`/api/shipments/${bookingId}/invoice?download=1`, '_blank', 'noopener,noreferrer')
  }

  const routePreview = useMemo(() => {
    if (!Array.isArray(routeCoordinates)) {
      return []
    }

    return routeCoordinates
      .map((point) => {
        if (Array.isArray(point) && point.length >= 2) {
          return [Number(point[0]), Number(point[1])]
        }

        if (point && typeof point === 'object') {
          return [Number(point.latitude ?? point.lat), Number(point.longitude ?? point.lng ?? point.lon)]
        }

        return [Number.NaN, Number.NaN]
      })
      .filter(([latitude, longitude]) => Number.isFinite(latitude) && Number.isFinite(longitude))
  }, [routeCoordinates])

  useEffect(() => {
    let mounted = true

    const loadBooking = async () => {
      if (booking?.id) {
        return
      }

      setLoading(true)
      setError('')

      try {
        const response = await fetchShipmentById(bookingId)
        if (!mounted) {
          return
        }

        setBooking(response.shipment)
      } catch (fetchError) {
        if (!mounted) {
          return
        }

        setError(fetchError instanceof Error ? fetchError.message : 'Unable to load shipment confirmation')
      } finally {
        if (mounted) {
          setLoading(false)
        }
      }
    }

    loadBooking()

    return () => {
      mounted = false
    }
  }, [booking?.id, bookingId])

  useEffect(() => {
    if (!bookingId) {
      return undefined
    }

    let mounted = true

    const loadTimeline = async () => {
      setTimelineLoading(true)
      try {
        const response = await fetchShipmentTimeline(bookingId)
        if (!mounted) {
          return
        }

        setTimeline(Array.isArray(response.timeline) ? response.timeline : [])
        if (!booking && response.shipment) {
          setBooking(response.shipment)
        }
      } catch {
        if (mounted) {
          setTimeline([])
        }
      } finally {
        if (mounted) {
          setTimelineLoading(false)
        }
      }
    }

    loadTimeline()
    const intervalId = window.setInterval(loadTimeline, 15000)

    return () => {
      mounted = false
      window.clearInterval(intervalId)
    }
  }, [booking, bookingId])

  useEffect(() => {
    if (!bookingId) {
      return undefined
    }

    let mounted = true

    const loadPayments = async () => {
      setPaymentLoading(true)

      try {
        const response = await fetchShipmentPayments(bookingId)
        if (!mounted) {
          return
        }

        setPayments(Array.isArray(response.payments) ? response.payments : [])
      } catch {
        if (mounted) {
          setPayments([])
        }
      } finally {
        if (mounted) {
          setPaymentLoading(false)
        }
      }
    }

    loadPayments()

    return () => {
      mounted = false
    }
  }, [bookingId])

  if (loading && !booking) {
    return (
      <section className="booking-confirmation-page">
        <div className="booking-confirmation-card">
          <p className="booking-kicker">Shipment confirmation</p>
          <h1>Loading your shipment</h1>
          <p className="booking-hero-copy">Retrieving the latest confirmation details and timeline.</p>
        </div>
      </section>
    )
  }

  if (error && !booking) {
    return (
      <section className="booking-confirmation-page">
        <div className="booking-confirmation-card">
          <p className="booking-kicker">Shipment confirmation</p>
          <h1>Unable to load shipment</h1>
          <p className="booking-hero-copy">{error}</p>
          <Link to="/shipments" className="booking-confirmation-link-inline">
            Back to shipment engine
          </Link>
        </div>
      </section>
    )
  }

  return (
    <section className="booking-confirmation-page">
      <div className="booking-confirmation-card booking-confirmation-card--hero">
        <p className="booking-kicker">Shipment confirmation</p>
        <h1>{bookingReference}</h1>
        <p className="booking-hero-copy">
          Your shipment has been registered and the operational timeline is now live.
        </p>

        <div className="booking-confirmation-chip-row">
          <span className="booking-confirmation-chip">{booking?.shipment_status || booking?.booking_status || 'pending'}</span>
          <span className="booking-confirmation-chip booking-confirmation-chip--subtle">{booking?.payment_status || 'pending'}</span>
          <span className="booking-confirmation-chip booking-confirmation-chip--subtle">{booking?.truck_type || '12 tyre'}</span>
        </div>
      </div>

      <div className="booking-confirmation-grid">
        <article className="booking-confirmation-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Shipment summary</p>
              <h2>Order details</h2>
            </div>
          </div>

          <div className="booking-modal-grid booking-confirmation-grid-inner">
            <div>
              <span>Customer</span>
              <strong>{booking?.customer_name || 'Pending'}</strong>
            </div>
            <div>
              <span>Phone</span>
              <strong>{booking?.phone || 'Pending'}</strong>
            </div>
            <div>
              <span>Route</span>
              <strong>{booking?.pickup_location || 'Pending'} to {booking?.drop_location || 'Pending'}</strong>
            </div>
            <div>
              <span>Price</span>
              <strong>{formatCurrency(booking?.estimated_price ?? booking?.price)}</strong>
            </div>
            <div>
              <span>Advance</span>
              <strong>{formatCurrency(Number((booking?.estimated_price ?? booking?.price) || 0) * 0.8)}</strong>
            </div>
            <div>
              <span>Created</span>
              <strong>{booking?.created_at || 'Pending'}</strong>
            </div>
          </div>

          <div className="booking-confirmation-actions">
            <Link to="/shipments" className="booking-pay-button booking-confirmation-link-inline">
              Back to shipment engine
            </Link>
            <Link to="/tracking" className="booking-confirmation-link-inline booking-confirmation-link-inline--secondary">
              Open tracking
            </Link>
            <button type="button" className="booking-pay-button booking-confirmation-link-inline booking-pay-button--secondary" onClick={downloadInvoice}>
              Download GST Invoice
            </button>
          </div>
        </article>

        <article className="booking-confirmation-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Tracking timeline</p>
              <h2>Live lifecycle</h2>
            </div>
            <span>{timelineLoading ? 'Loading...' : `${timeline.length} events`}</span>
          </div>

          <div className="booking-timeline-list">
            {timeline.length === 0 && !timelineLoading ? (
              <p className="booking-empty-state">No status updates have been logged yet.</p>
            ) : (
              timeline.map((entry) => (
                <article key={`${entry.id}-${entry.created_at}`} className="booking-timeline-item">
                  <div className="booking-timeline-dot" />
                  <div>
                    <strong>{entry.status}</strong>
                    <p>{entry.note || 'Status update recorded.'}</p>
                    <span>{entry.location || 'System'} · {entry.created_at}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </article>

          <article className="booking-confirmation-card">
            <div className="booking-history-header">
              <div>
                <p className="booking-form-label">Route preview</p>
                <h2>Map route visualization</h2>
              </div>
              <span>{routePreview.length > 1 ? `${routePreview.length} route points` : 'Pending route'}</span>
            </div>

            {routePreview.length > 1 ? (
              <div className="booking-route-map-frame">
                <MapContainer center={routePreview[0]} zoom={5} scrollWheelZoom={false} className="booking-route-map">
                  <TileLayer
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  />
                  <Polyline positions={routePreview} pathOptions={{ color: '#f97316', weight: 4, opacity: 0.9 }} />
                </MapContainer>
              </div>
            ) : (
              <p className="booking-empty-state">Route preview will appear after the shipment is created.</p>
            )}
          </article>

        <article className="booking-confirmation-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Payment history</p>
              <h2>Settlement ledger</h2>
            </div>
            <span>{paymentLoading ? 'Loading...' : `${payments.length} records`}</span>
          </div>

          <div className="booking-history-list booking-payment-history-list">
            {payments.length === 0 && !paymentLoading ? (
              <p className="booking-empty-state">No payments have been recorded for this shipment yet.</p>
            ) : (
              payments.map((payment) => (
                <article key={`${payment.id}-${payment.razorpay_order_id}`} className="booking-history-item booking-payment-history-item">
                  <div>
                    <strong>{payment.payment_type || 'advance'} payment</strong>
                    <p>{payment.razorpay_order_id || 'Pending order reference'}</p>
                  </div>
                  <div className="booking-payment-history-meta">
                    <span>{formatCurrency(payment.amount)}</span>
                    <span>{payment.payment_status || 'created'}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </article>
      </div>
    </section>
  )
}
