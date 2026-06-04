import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { io } from 'socket.io-client'
import { SOCKET_BASE_URL } from '../services/apiBase'
import {
  createShipment,
  createShipmentPaymentOrder,
  fetchMyShipments,
  fetchShipmentById,
  fetchShipmentEvents,
  fetchShipmentPayments,
  fetchShipmentTimeline,
  fetchShipmentActivityFeed,
  fetchRouteIntelligence,
  fetchRouteSuggestions,
  recommendVehicle,
  verifyShipmentPayment,
  fetchUserPreferences,
  deleteUserPreferences,
  saveUserPreferences,
  logUserSearch,
  fetchUserRecommendations,
} from '../services/shipmentService'
import { useAuth } from '../context/AuthContext'
import { EmptyState } from './AppChrome'
import './Booking.css'

const initialFormState = {
  customer_name: '',
  phone: '',
  pickup_location: '',
  drop_location: '',
  truck_type: '12 tyre',
  load_weight: '',
}

const truckTypes = [
  { value: '12 tyre', label: '12 Tyre', maxLoad: 'Up to 25 tons' },
  { value: '14 tyre', label: '14 Tyre', maxLoad: 'Up to 30 tons' },
  { value: '16 tyre', label: '16 Tyre', maxLoad: 'Up to 35 tons' },
]

const ADVANCE_RATIO = 0.8
const NOTIFICATION_SETTINGS_KEY = 'skdls_whatsapp_notification_settings'

const toastTimeoutMs = 3600

const defaultNotificationSettings = {
  whatsapp_notifications_enabled: true,
  whatsapp_booking_confirmation: true,
  whatsapp_payment_confirmation: true,
  whatsapp_live_location: true,
  whatsapp_delivery_updates: true,
}

const defaultPreferenceDraft = {
  preferred_truck_types: [],
  preferred_contact_method: 'whatsapp',
  delivery_notes: '',
}

let razorpayScriptPromise = null

function ToastStack({ toasts, onClose }) {
  return (
    <div className="booking-toast-stack" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div key={toast.id} className={`booking-toast ${toast.type}`}>
          <strong>{toast.title}</strong>
          <p>{toast.message}</p>
          <button type="button" onClick={() => onClose(toast.id)}>Dismiss</button>
        </div>
      ))}
    </div>
  )
}

function loadRazorpayScript() {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('Razorpay can only be loaded in the browser'))
  }

  if (window.Razorpay) {
    return Promise.resolve(true)
  }

  if (!razorpayScriptPromise) {
    razorpayScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = 'https://checkout.razorpay.com/v1/checkout.js'
      script.async = true
      script.onload = () => resolve(true)
      script.onerror = () => reject(new Error('Failed to load Razorpay checkout'))
      document.body.appendChild(script)
    })
  }

  return razorpayScriptPromise
}

function formatPrice(price) {
  if (price === null || price === undefined || Number.isNaN(Number(price))) {
    return 'Pending'
  }

  return `₹${Number(price).toLocaleString('en-IN')}`
}

function normalizePhoneNumber(value) {
  return String(value || '').replace(/[\s()-]/g, '').trim()
}

function isValidPhoneNumber(value) {
  const normalized = normalizePhoneNumber(value)
  const digits = normalized.startsWith('+') ? normalized.slice(1) : normalized
  return /^\+?\d{7,15}$/.test(normalized) && digits.length >= 7 && digits.length <= 15
}

function normalizePreferenceDraft(preferences = {}) {
  const preferredTruckTypes = Array.isArray(preferences.preferred_truck_types)
    ? preferences.preferred_truck_types
    : typeof preferences.preferred_truck_types === 'string'
      ? preferences.preferred_truck_types.split(',').map((item) => item.trim()).filter(Boolean)
      : []

  return {
    preferred_truck_types: preferredTruckTypes,
    preferred_contact_method: preferences.preferred_contact_method || 'whatsapp',
    delivery_notes: preferences.delivery_notes || preferences.notes || '',
  }
}

export default function Booking() {
  const { ready: authReady } = useAuth()
  const [formState, setFormState] = useState(initialFormState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [phoneError, setPhoneError] = useState('')
  const [fieldErrors, setFieldErrors] = useState({})
  const [bookingResult, setBookingResult] = useState(null)
  const [bookingReference, setBookingReference] = useState('')
  const [paymentLoading, setPaymentLoading] = useState(false)
  const [paymentError, setPaymentError] = useState('')
  const [paymentSuccess, setPaymentSuccess] = useState(null)
  const [notificationSettings, setNotificationSettings] = useState(defaultNotificationSettings)
  const [activeStep, setActiveStep] = useState(0)
  const [vehicleRecommendation, setVehicleRecommendation] = useState(null)
  const [recommendationLoading, setRecommendationLoading] = useState(false)
  const [recommendationError, setRecommendationError] = useState('')
  const [bookingHistory, setBookingHistory] = useState([])
  const [selectedHistoryBookingId, setSelectedHistoryBookingId] = useState(null)
  const [bookingTimeline, setBookingTimeline] = useState([])
  const [shipmentEvents, setShipmentEvents] = useState([])
  const [paymentHistory, setPaymentHistory] = useState([])
  const [activityFeed, setActivityFeed] = useState([])
  const [activeShipments, setActiveShipments] = useState([])
  const [recentShipments, setRecentShipments] = useState([])
  const [activitySummary, setActivitySummary] = useState({})
  useEffect(() => {
    const socket = window.socket
    if (!socket) return
    const onShipmentActivity = (payload) => {
      if (!payload) return
      // prepend new feed items if present
      if (payload.feed && Array.isArray(payload.feed)) {
        setActivityFeed(prev => {
          const ids = new Set(prev.map(i => i.id || i.event_id))
          const newItems = payload.feed.filter(i => !(ids.has(i.id) || ids.has(i.event_id)))
          return [...newItems, ...prev]
        })
      }
      if (payload.active_shipments) setActiveShipments(payload.active_shipments)
      if (payload.recent_shipments) setRecentShipments(payload.recent_shipments)
      if (payload.summary) setActivitySummary(payload.summary)
    }

    socket.on('shipment:activity', onShipmentActivity)
    return () => socket.off('shipment:activity', onShipmentActivity)
  }, [])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [paymentHistoryLoading, setPaymentHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [eventsError, setEventsError] = useState('')
  const [paymentHistoryError, setPaymentHistoryError] = useState('')
  const [liveEstimate, setLiveEstimate] = useState(null)
  const [estimateLoading, setEstimateLoading] = useState(false)
  const [routeSearchError, setRouteSearchError] = useState('')
  const [routeIntelligence, setRouteIntelligence] = useState({
    pickup_suggestions: [],
    drop_suggestions: [],
    recent_searches: [],
    popular_routes: [],
    frequently_booked_routes: [],
    personalized_suggestions: [],
    routes: [],
    live_estimate: null,
  })
  const [personalizedRecommendations, setPersonalizedRecommendations] = useState([])
  const [userPreferences, setUserPreferences] = useState({})
  const [preferenceDraft, setPreferenceDraft] = useState(defaultPreferenceDraft)
  const [preferenceSaving, setPreferenceSaving] = useState(false)
  const [preferenceResetting, setPreferenceResetting] = useState(false)
  const [toastList, setToastList] = useState([])
  const historyPreview = bookingHistory
  const selectedHistoryPreview = historyPreview.find((item) => item.id === selectedHistoryBookingId) || historyPreview[0] || null

  const selectedTruck = useMemo(
    () => truckTypes.find((item) => item.value === formState.truck_type) || truckTypes[0],
    [formState.truck_type],
  )

  const pickupAutocomplete = Array.isArray(routeIntelligence.pickup_suggestions) ? routeIntelligence.pickup_suggestions : []
  const dropAutocomplete = Array.isArray(routeIntelligence.drop_suggestions) ? routeIntelligence.drop_suggestions : []
  const recentSearchRoutes = Array.isArray(routeIntelligence.recent_searches) ? routeIntelligence.recent_searches : []
  const popularRoutes = Array.isArray(routeIntelligence.popular_routes) ? routeIntelligence.popular_routes : []
  const frequentRoutes = Array.isArray(routeIntelligence.frequently_booked_routes) ? routeIntelligence.frequently_booked_routes : []
  const personalizedRoutes = Array.isArray(routeIntelligence.personalized_suggestions) ? routeIntelligence.personalized_suggestions : []
  const recommendedRoutes = Array.isArray(routeIntelligence.routes) ? routeIntelligence.routes : []

  const pushToast = (type, title, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setToastList((previous) => [...previous, { id, type, title, message }])
    window.setTimeout(() => {
      setToastList((previous) => previous.filter((item) => item.id !== id))
    }, toastTimeoutMs)
  }

  const closeToast = (toastId) => {
    setToastList((previous) => previous.filter((item) => item.id !== toastId))
  }

  const bookingSteps = useMemo(
    () => [
      {
        id: 1,
        label: 'Route',
        complete: Boolean(formState.customer_name.trim() && formState.phone.trim()),
      },
      {
        id: 2,
        label: 'Load',
        complete: Boolean(formState.pickup_location.trim() && formState.drop_location.trim() && formState.load_weight),
      },
      {
        id: 3,
        label: 'Vehicle',
        complete: Boolean(vehicleRecommendation?.recommended_vehicle),
      },
      {
        id: 4,
        label: 'Confirm',
        complete: Boolean(bookingResult?.id),
      },
    ],
    [bookingResult?.id, formState.customer_name, formState.drop_location, formState.load_weight, formState.phone, formState.pickup_location, selectedTruck?.value, vehicleRecommendation?.recommended_vehicle],
  )

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(NOTIFICATION_SETTINGS_KEY)
      if (!stored) {
        return
      }

      const parsed = JSON.parse(stored)
      setNotificationSettings((previous) => ({
        ...previous,
        ...parsed,
      }))
    } catch {
      setNotificationSettings(defaultNotificationSettings)
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(NOTIFICATION_SETTINGS_KEY, JSON.stringify(notificationSettings))
  }, [notificationSettings])

  useEffect(() => {
    const nextStep = bookingSteps.reduce((current, step) => (step.complete ? step.id : current), 0)
    setActiveStep(nextStep)
  }, [bookingSteps])

  useEffect(() => {
    const pickupLocation = formState.pickup_location.trim()
    const dropLocation = formState.drop_location.trim()
    const loadWeight = Number(formState.load_weight)
    const truckType = formState.truck_type.trim()

    if (!pickupLocation || !dropLocation || !truckType || !Number.isFinite(loadWeight) || loadWeight <= 0) {
      setVehicleRecommendation(null)
      setRecommendationError('')
      return undefined
    }

    let mounted = true
    setRecommendationLoading(true)
    setRecommendationError('')

    const timeoutId = window.setTimeout(async () => {
      try {
        const response = await recommendVehicle({
          pickup_location: pickupLocation,
          drop_location: dropLocation,
          load_weight: loadWeight,
          truck_type: truckType,
        })

        if (!mounted) {
          return
        }

        setVehicleRecommendation(response)
      } catch (recommendError) {
        if (!mounted) {
          return
        }

        setVehicleRecommendation(null)
        setRecommendationError(recommendError instanceof Error ? recommendError.message : 'Unable to recommend a vehicle right now.')
      } finally {
        if (mounted) {
          setRecommendationLoading(false)
        }
      }
    }, 450)

    return () => {
      mounted = false
      window.clearTimeout(timeoutId)
    }
  }, [formState.drop_location, formState.load_weight, formState.pickup_location, formState.truck_type])

  useEffect(() => {
    const loadHistory = async () => {
      if (!authReady) {
        return
      }

      setHistoryLoading(true)
      setHistoryError('')

      try {
        const response = await fetchMyShipments()
        const nextHistory = Array.isArray(response.shipments) ? response.shipments : []
        setBookingHistory(nextHistory)

        if (!selectedHistoryBookingId && nextHistory.length > 0) {
          setSelectedHistoryBookingId(nextHistory[0].id)
        }
      } catch (historyFetchError) {
        setHistoryError(historyFetchError instanceof Error ? historyFetchError.message : 'Unable to load booking history')
        setBookingHistory([])
      } finally {
        setHistoryLoading(false)
      }
    }

    loadHistory()
  }, [authReady, selectedHistoryBookingId])

  useEffect(() => {
    if (!authReady) return
    let mounted = true
    const loadPrefsAndRecs = async () => {
      try {
        const prefsResp = await fetchUserPreferences()
        if (!mounted) return
        const nextPreferences = prefsResp.preferences || {}
        setUserPreferences(nextPreferences)
        setPreferenceDraft(normalizePreferenceDraft(nextPreferences))
      } catch (e) {
        // ignore
      }

      try {
        const recs = await fetchUserRecommendations({ limit: 6 })
        if (!mounted) return
        setPersonalizedRecommendations(Array.isArray(recs.recommendations) ? recs.recommendations : [])
      } catch (e) {
        // ignore
      }
    }

    loadPrefsAndRecs()
    return () => { mounted = false }
  }, [authReady])

  useEffect(() => {
    let mounted = true
    const pickupLocation = formState.pickup_location.trim()
    const dropLocation = formState.drop_location.trim()
    const truckType = formState.truck_type.trim()
    const loadWeight = Number(formState.load_weight)

    setEstimateLoading(true)
    setRouteSearchError('')

    const timeoutId = window.setTimeout(async () => {
      try {
        const [pickupResponse, dropResponse, routeResponse] = await Promise.all([
          fetchRouteSuggestions({
            field: 'pickup',
            q: pickupLocation,
            pickup_location: pickupLocation,
            drop_location: dropLocation,
            load_weight: Number.isFinite(loadWeight) ? loadWeight : undefined,
            truck_type: truckType,
            limit: 6,
          }),
          fetchRouteSuggestions({
            field: 'drop',
            q: dropLocation,
            pickup_location: pickupLocation,
            drop_location: dropLocation,
            load_weight: Number.isFinite(loadWeight) ? loadWeight : undefined,
            truck_type: truckType,
            limit: 6,
          }),
          fetchRouteIntelligence({
            pickup_location: pickupLocation,
            drop_location: dropLocation,
            load_weight: Number.isFinite(loadWeight) ? loadWeight : undefined,
            truck_type: truckType,
            limit: 6,
          }),
        ])

        if (!mounted) {
          return
        }

        const nextIntelligence = {
          pickup_suggestions: Array.isArray(pickupResponse.pickup_suggestions) && pickupResponse.pickup_suggestions.length > 0 ? pickupResponse.pickup_suggestions : Array.isArray(pickupResponse.suggestions) ? pickupResponse.suggestions : routeResponse.pickup_suggestions || [],
          drop_suggestions: Array.isArray(dropResponse.drop_suggestions) && dropResponse.drop_suggestions.length > 0 ? dropResponse.drop_suggestions : Array.isArray(dropResponse.suggestions) ? dropResponse.suggestions : routeResponse.drop_suggestions || [],
          recent_searches: routeResponse.recent_searches || pickupResponse.recent_searches || [],
          popular_routes: routeResponse.popular_routes || pickupResponse.popular_routes || [],
          frequently_booked_routes: routeResponse.frequently_booked_routes || pickupResponse.frequently_booked_routes || [],
          personalized_suggestions: routeResponse.personalized_suggestions || pickupResponse.personalized_suggestions || [],
          routes: routeResponse.routes || [],
          live_estimate: routeResponse.live_estimate || pickupResponse.live_estimate || null,
        }

        setRouteIntelligence(nextIntelligence)
        setLiveEstimate(nextIntelligence.live_estimate)
        // Log user searches for personalization (non-blocking)
        try {
          if (pickupLocation) logUserSearch(pickupLocation, { field: 'pickup' }).catch(() => {})
        } catch (e) {}
        try {
          if (dropLocation) logUserSearch(dropLocation, { field: 'drop' }).catch(() => {})
        } catch (e) {}
      } catch (routeSearchFetchError) {
        if (!mounted) {
          return
        }

        setRouteSearchError(routeSearchFetchError instanceof Error ? routeSearchFetchError.message : 'Unable to load route suggestions right now.')
        setRouteIntelligence({
          pickup_suggestions: [],
          drop_suggestions: [],
          recent_searches: [],
          popular_routes: [],
          frequently_booked_routes: [],
          personalized_suggestions: [],
          routes: [],
          live_estimate: null,
        })
        setLiveEstimate(null)
      } finally {
        if (mounted) {
          setEstimateLoading(false)
        }
      }
    }, 450)

    return () => {
      mounted = false
      window.clearTimeout(timeoutId)
    }
  }, [formState.drop_location, formState.load_weight, formState.pickup_location, formState.truck_type])

  useEffect(() => {
    const loadTimeline = async () => {
      if (!selectedHistoryBookingId) {
        setBookingTimeline([])
        setShipmentEvents([])
        return
      }

      setTimelineLoading(true)
      setEventsLoading(true)

      try {
        const [timelineResponse, eventsResponse] = await Promise.all([
          fetchShipmentTimeline(selectedHistoryBookingId),
          fetchShipmentEvents(selectedHistoryBookingId),
        ])

        setBookingTimeline(Array.isArray(timelineResponse.timeline) ? timelineResponse.timeline : [])
        setShipmentEvents(Array.isArray(eventsResponse.events) ? eventsResponse.events : [])
        setEventsError('')
      } catch (loadError) {
        setBookingTimeline([])
        setShipmentEvents([])
        setEventsError(loadError instanceof Error ? loadError.message : 'Unable to load shipment activity')
      } finally {
        setTimelineLoading(false)
        setEventsLoading(false)
      }
    }

    loadTimeline()
    const intervalId = window.setInterval(loadTimeline, 15000)

    return () => {
      window.clearInterval(intervalId)
    }
  }, [selectedHistoryBookingId])

  useEffect(() => {
    let mounted = true
    const loadActivityFeed = async () => {
      try {
        const response = await fetchShipmentActivityFeed({ limit: 12, shipment_id: selectedHistoryBookingId })
        if (!mounted) return
        setActivityFeed(Array.isArray(response.feed) ? response.feed : [])
        setActiveShipments(Array.isArray(response.active_shipments) ? response.active_shipments : [])
        setRecentShipments(Array.isArray(response.recent_shipments) ? response.recent_shipments : [])
        setActivitySummary(response.summary || {})
      } catch (err) {
        // ignore silently
      }
    }

    loadActivityFeed()
    const interval = window.setInterval(loadActivityFeed, 10000)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [selectedHistoryBookingId])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return undefined
    }

    const socket = io(SOCKET_BASE_URL, {
      path: '/socket.io',
      transports: ['websocket', 'polling'],
      withCredentials: true,
      reconnection: true,
      reconnectionDelayMax: 5000,
    })

    const refreshJourney = () => {
      if (!selectedHistoryBookingId) {
        return
      }

      fetchShipmentTimeline(selectedHistoryBookingId)
        .then((response) => setBookingTimeline(Array.isArray(response.timeline) ? response.timeline : []))
        .catch(() => {})

      fetchShipmentEvents(selectedHistoryBookingId)
        .then((response) => setShipmentEvents(Array.isArray(response.events) ? response.events : []))
        .catch(() => {})
    }

    const liveEvents = ['shipment:update', 'shipment:activity', 'payment:update', 'driver:update', 'eta:update', 'delivery:confirmed', 'notification:update']

    liveEvents.forEach((eventName) => {
      socket.on(eventName, refreshJourney)
    })

    return () => {
      liveEvents.forEach((eventName) => socket.off(eventName, refreshJourney))
      socket.disconnect()
    }
  }, [selectedHistoryBookingId])

  const validateForm = (values = formState) => {
    const nextErrors = {}

    if (!String(values.customer_name || '').trim()) {
      nextErrors.customer_name = 'Customer name is required.'
    }

    if (!isValidPhoneNumber(values.phone)) {
      nextErrors.phone = 'Use a valid WhatsApp number with 7 to 15 digits.'
    }

    if (!String(values.pickup_location || '').trim()) {
      nextErrors.pickup_location = 'Pickup location is required.'
    }

    if (!String(values.drop_location || '').trim()) {
      nextErrors.drop_location = 'Drop location is required.'
    }

    if (!Number.isFinite(Number(values.load_weight)) || Number(values.load_weight) <= 0) {
      nextErrors.load_weight = 'Enter a load weight greater than zero.'
    }

    return nextErrors
  }

  useEffect(() => {
    const loadPaymentHistory = async () => {
      if (!selectedHistoryBookingId) {
        setPaymentHistory([])
        return
      }

      setPaymentHistoryLoading(true)
      setPaymentHistoryError('')

      try {
        const response = await fetchShipmentPayments(selectedHistoryBookingId)
        setPaymentHistory(Array.isArray(response.payments) ? response.payments : [])
      } catch (paymentHistoryFetchError) {
        setPaymentHistoryError(paymentHistoryFetchError instanceof Error ? paymentHistoryFetchError.message : 'Unable to load payment history')
        setPaymentHistory([])
      } finally {
        setPaymentHistoryLoading(false)
      }
    }

    loadPaymentHistory()
  }, [selectedHistoryBookingId])

  const handleChange = (event) => {
    const { name, value } = event.target

    if (name === 'phone') {
      setPhoneError('')
    }

    if (name === 'pickup_location' || name === 'drop_location' || name === 'load_weight' || name === 'truck_type') {
      setRouteSearchError('')
    }

    setFieldErrors((previous) => {
      if (!(name in previous)) {
        return previous
      }

      const nextErrors = { ...previous }
      delete nextErrors[name]
      return nextErrors
    })

    setFormState((previous) => ({
      ...previous,
      [name]: value,
    }))
  }

  const handleRouteSuggestionSelect = (suggestion, field = 'pickup') => {
    if (!suggestion) {
      return
    }

    const pickupLocation = String(suggestion.pickup_location || suggestion.value || suggestion.label || '').trim()
    const dropLocation = String(suggestion.drop_location || suggestion.value || suggestion.label || '').trim()
    const truckType = String(suggestion.truck_type || '').trim()
    const suggestionKind = String(suggestion.kind || '').toLowerCase()
    const isRouteSuggestion = Boolean(dropLocation && pickupLocation && suggestionKind !== 'location' && suggestionKind !== 'pickup' && suggestionKind !== 'drop')

    setRouteSearchError('')

    setFormState((previous) => ({
      ...previous,
      pickup_location: isRouteSuggestion || field === 'pickup' ? pickupLocation || previous.pickup_location : previous.pickup_location,
      drop_location: isRouteSuggestion || field === 'drop' ? dropLocation || previous.drop_location : previous.drop_location,
      truck_type: truckType || previous.truck_type,
    }))
  }

  const renderRouteChips = (items, field = 'route', chipClassName = 'booking-route-chip') => (
    <div className="booking-route-chip-list">
      {items.slice(0, 6).map((item) => {
        const label = String(item.label || item.value || item.route_label || '').trim()
        const meta = [item.count ? `${item.count} trips` : '', item.truck_type || '', item.eta_hours ? `${item.eta_hours} hrs` : '']
          .filter(Boolean)
          .join(' · ')

        return (
          <button
            key={`${field}-${item.route_key || label}`}
            type="button"
            className={chipClassName}
            onClick={() => handleRouteSuggestionSelect(item, field)}
          >
            <strong>{label || 'Suggested route'}</strong>
            {meta && <span>{meta}</span>}
          </button>
        )
      })}
    </div>
  )

  const handleNotificationToggle = (name) => {
    setNotificationSettings((previous) => ({
      ...previous,
      ...(name === 'whatsapp_notifications_enabled'
        ? (() => {
            const enabled = !previous[name]
            return {
              whatsapp_notifications_enabled: enabled,
              whatsapp_booking_confirmation: enabled,
              whatsapp_payment_confirmation: enabled,
              whatsapp_live_location: enabled,
              whatsapp_delivery_updates: enabled,
            }
          })()
        : (() => {
            const nextValue = !previous[name]
            const nextSettings = {
              ...previous,
              [name]: nextValue,
            }

            const anyEnabled =
              nextSettings.whatsapp_booking_confirmation ||
              nextSettings.whatsapp_payment_confirmation ||
              nextSettings.whatsapp_live_location ||
              nextSettings.whatsapp_delivery_updates

            return {
              ...nextSettings,
              whatsapp_notifications_enabled: anyEnabled,
            }
          })()),
    }))
  }

  const closeSuccessModal = () => {
    setBookingResult(null)
  }

  const closePaymentSuccessModal = () => {
    setPaymentSuccess(null)
  }

  const getAdvanceAmount = () => {
    const price = Number(bookingResult?.estimated_price ?? bookingResult?.price ?? 0)
    if (!Number.isFinite(price) || price <= 0) {
      return 0
    }

    return Math.round(price * ADVANCE_RATIO)
  }

  const downloadInvoice = () => {
    if (!bookingResult?.id) {
      return
    }

    window.open(`/api/shipments/${bookingResult.id}/invoice?download=1`, '_blank', 'noopener,noreferrer')
  }

  const handlePay = async (paymentType = 'advance') => {
    if (!bookingResult?.id || paymentLoading) {
      return
    }

    setPaymentError('')
    setPaymentLoading(true)

    try {
      await loadRazorpayScript()
      const orderData = await createShipmentPaymentOrder(bookingResult.id, paymentType)

      const paymentLabel = paymentType === 'full' ? 'Full shipment payment' : 'Advance payment'

      const razorpay = new window.Razorpay({
        key: orderData.key_id,
        amount: orderData.amount,
        currency: orderData.currency,
        name: 'SKDLS Transportations',
        description: `${paymentLabel} for shipment #${bookingResult.id}`,
        order_id: orderData.order_id,
        prefill: {
          name: bookingResult.customer_name || formState.customer_name || '',
          contact: bookingResult.phone || formState.phone || '',
        },
        theme: {
          color: '#f97316',
        },
        modal: {
          ondismiss: () => {
            setPaymentLoading(false)
          },
        },
        handler: async (response) => {
          try {
            const verified = await verifyShipmentPayment({
              shipment_id: bookingResult.id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
              payment_type: paymentType,
            })

            let refreshedBooking = verified.shipment
            if (!refreshedBooking?.id) {
              const refreshed = await fetchShipmentById(bookingResult.id)
              refreshedBooking = refreshed.shipment
            }

            setBookingResult(refreshedBooking)
            setPaymentSuccess({
              paymentId: verified.payment?.razorpay_payment_id || response.razorpay_payment_id,
              orderId: verified.payment?.razorpay_order_id || response.razorpay_order_id,
              amount: verified.payment?.amount || orderData.amount / 100,
              paymentType,
              bookingId: bookingResult.id,
              bookingStatus: refreshedBooking?.shipment_status || 'confirmed',
            })

            const refreshedPayments = await fetchShipmentPayments(bookingResult.id)
            setPaymentHistory(Array.isArray(refreshedPayments.payments) ? refreshedPayments.payments : [])
            pushToast('success', 'Payment verified', 'Shipment payment was confirmed successfully')
          } catch (verificationError) {
            setPaymentError(verificationError instanceof Error ? verificationError.message : 'Payment verification failed.')
          } finally {
            setPaymentLoading(false)
          }
        },
      })

      razorpay.on('payment.failed', (response) => {
        setPaymentError(response?.error?.description || 'Payment failed. Please try again.')
        setPaymentLoading(false)
      })

      razorpay.open()
    } catch (paymentRequestError) {
      setPaymentError(paymentRequestError instanceof Error ? paymentRequestError.message : 'Unable to start payment.')
      setPaymentLoading(false)
    }
  }

  const handlePayAdvance = async () => handlePay('advance')

  const handlePayFull = async () => handlePay('full')

  const handlePreferenceChange = (field, value) => {
    setPreferenceDraft((previous) => ({
      ...previous,
      [field]: value,
    }))
  }

  const handleSavePreferences = async () => {
    setPreferenceSaving(true)
    try {
      const payload = {
        ...userPreferences,
        ...preferenceDraft,
        preferred_truck_types: Array.isArray(preferenceDraft.preferred_truck_types)
          ? preferenceDraft.preferred_truck_types
          : String(preferenceDraft.preferred_truck_types || '')
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
      }
      const response = await saveUserPreferences(payload)
      const nextPreferences = response.preferences || payload
      setUserPreferences(nextPreferences)
      setPreferenceDraft(normalizePreferenceDraft(nextPreferences))
      pushToast('success', 'Preferences saved', 'Your booking preferences now personalize future recommendations.')
    } catch (saveError) {
      pushToast('error', 'Preferences not saved', saveError instanceof Error ? saveError.message : 'Unable to save preferences right now.')
    } finally {
      setPreferenceSaving(false)
    }
  }

  const handleResetPreferences = async () => {
    setPreferenceResetting(true)
    try {
      await deleteUserPreferences()
      const nextPreferences = {}
      setUserPreferences(nextPreferences)
      setPreferenceDraft(normalizePreferenceDraft(nextPreferences))
      pushToast('success', 'Preferences reset', 'Stored booking personalization has been cleared.')
    } catch (resetError) {
      pushToast('error', 'Preferences not reset', resetError instanceof Error ? resetError.message : 'Unable to clear preferences right now.')
    } finally {
      setPreferenceResetting(false)
    }
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    const validationErrors = validateForm()
    setFieldErrors(validationErrors)

    if (Object.keys(validationErrors).length > 0) {
      setLoading(false)
      return
    }

    const normalizedPhone = normalizePhoneNumber(formState.phone)
    if (!isValidPhoneNumber(normalizedPhone)) {
      setPhoneError('Enter a valid phone number with 7 to 15 digits. Use + country code for WhatsApp delivery.')
      setLoading(false)
      return
    }

    setPhoneError('')

    try {
      const response = await createShipment({
        ...formState,
        phone: normalizedPhone,
        load_weight: Number(formState.load_weight),
        notification_settings: notificationSettings,
        ...notificationSettings,
      })
      const booking = response.shipment || {
        id: response.shipment_id,
        estimated_price: response.estimated_price,
        shipment_status: response.shipment_status,
      }

      setBookingResult(booking)
      setBookingReference(booking.booking_reference || `Shipment #${booking.id}`)
      setSelectedHistoryBookingId(booking.id)
      if (response.route_coordinates) {
        setLiveEstimate((current) => ({
          ...(current || {}),
          route_coordinates: response.route_coordinates,
        }))
      }
      pushToast('success', 'Shipment created', 'Your shipment has been registered and is ready for payment')
      setFormState(initialFormState)
      setFieldErrors({})
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to create shipment right now.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="booking-page">
      <section className="booking-stepper-card" aria-label="Shipment flow progress">
        {bookingSteps.map((step) => (
          <div key={step.id} className={`booking-step ${activeStep >= step.id ? 'is-active' : ''}`}>
            <span className="booking-step-index">0{step.id}</span>
            <div>
              <strong>{step.label}</strong>
              <p>{step.complete ? 'Complete' : 'In progress'}</p>
            </div>
          </div>
        ))}
      </section>

      <section className="booking-shell">
        <div className="booking-hero-card">
          <p className="booking-kicker">Shipment booking</p>
          <h1>Reserve freight, pay online, and track every mile.</h1>
          <p className="booking-hero-copy">
            Create a shipment request in one step, get a live price estimate, pay by Razorpay, and keep the same dark operations
            theme across the workflow.
          </p>

          <div className="booking-hero-metrics" aria-label="Shipment highlights">
            <article>
              <strong>Instant ID</strong>
              <span>Backend-generated on submit</span>
            </article>
            <article>
              <strong>Validated input</strong>
              <span>Phone, route, truck, and load checks</span>
            </article>
            <article>
              <strong>Live pricing</strong>
              <span>Calculated by the backend</span>
            </article>
            <article>
              <strong>AI recommendation</strong>
              <span>{recommendationLoading ? 'Analyzing fleet...' : vehicleRecommendation?.recommended_vehicle?.vehicle_name || selectedTruck.label}</span>
            </article>
            {Array.isArray(userPreferences.preferred_truck_types) && userPreferences.preferred_truck_types.length > 0 && (
              <article>
                <strong>Preferred trucks</strong>
                <span>{userPreferences.preferred_truck_types.slice(0,3).join(', ')}</span>
              </article>
            )}
          </div>

            <div className="booking-preferences-panel">
              <div className="booking-preferences-heading">
                <div>
                  <p className="booking-form-label">Personalization</p>
                  <h3>Save routing preferences</h3>
                </div>
                <div className="booking-preferences-actions">
                  <button type="button" className="booking-secondary-button" onClick={handleResetPreferences} disabled={preferenceResetting || preferenceSaving}>
                    {preferenceResetting ? 'Resetting...' : 'Reset preferences'}
                  </button>
                  <button type="button" className="booking-secondary-button" onClick={handleSavePreferences} disabled={preferenceSaving || preferenceResetting}>
                    {preferenceSaving ? 'Saving...' : 'Save preferences'}
                  </button>
                </div>
              </div>

              <div className="booking-preferences-grid">
                <label className="booking-field">
                  <span>Preferred truck types</span>
                  <input
                    type="text"
                    value={Array.isArray(preferenceDraft.preferred_truck_types) ? preferenceDraft.preferred_truck_types.join(', ') : preferenceDraft.preferred_truck_types || ''}
                    onChange={(event) => handlePreferenceChange('preferred_truck_types', event.target.value)}
                    placeholder="12 tyre, 14 tyre"
                  />
                </label>

                <label className="booking-field">
                  <span>Preferred contact method</span>
                  <select
                    value={preferenceDraft.preferred_contact_method || 'whatsapp'}
                    onChange={(event) => handlePreferenceChange('preferred_contact_method', event.target.value)}
                  >
                    <option value="whatsapp">WhatsApp</option>
                    <option value="phone">Phone call</option>
                    <option value="sms">SMS</option>
                    <option value="email">Email</option>
                  </select>
                </label>

                <label className="booking-field booking-field-wide">
                  <span>Delivery notes</span>
                  <textarea
                    value={preferenceDraft.delivery_notes || ''}
                    onChange={(event) => handlePreferenceChange('delivery_notes', event.target.value)}
                    rows="3"
                    placeholder="Dock timing, driver instructions, or handling notes"
                  />
                </label>
              </div>
            </div>
        </div>

        <div className="booking-form-card">
          <div className="booking-form-header">
            <div>
              <p className="booking-form-label">New shipment</p>
              <h2>Transport request</h2>
            </div>
            <span className="booking-status-pill">{selectedTruck.label}</span>
          </div>

          <form className="booking-form" onSubmit={handleSubmit}>
            <div className="booking-grid">
              <label className="booking-field">
                <span>Customer name</span>
                <input
                  name="customer_name"
                  type="text"
                  value={formState.customer_name}
                  onChange={handleChange}
                  placeholder="Enter customer name"
                  aria-invalid={Boolean(fieldErrors.customer_name)}
                  required
                />
                {fieldErrors.customer_name && <small className="booking-field-error">{fieldErrors.customer_name}</small>}
              </label>

              <label className="booking-field">
                <span>Phone number</span>
                <input
                  name="phone"
                  type="tel"
                  value={formState.phone}
                  onChange={handleChange}
                  placeholder="Enter WhatsApp-ready phone number"
                  inputMode="tel"
                  autoComplete="tel"
                  pattern="^\\+?[0-9]{7,15}$"
                  aria-invalid={Boolean(fieldErrors.phone || phoneError)}
                  required
                />
                <small className="booking-field-hint">Use a valid WhatsApp number with 7 to 15 digits. Include + country code for best delivery.</small>
                {fieldErrors.phone && <small className="booking-field-error">{fieldErrors.phone}</small>}
              </label>

              <label className="booking-field booking-field-wide">
                <span>Pickup location</span>
                <input
                  name="pickup_location"
                  type="text"
                  value={formState.pickup_location}
                  onChange={handleChange}
                  placeholder="Pickup city, yard, warehouse, or terminal"
                  aria-invalid={Boolean(fieldErrors.pickup_location)}
                  required
                />
                {fieldErrors.pickup_location && <small className="booking-field-error">{fieldErrors.pickup_location}</small>}
                {pickupAutocomplete.length > 0 && (
                  <div className="booking-autocomplete-panel" aria-label="Pickup location suggestions">
                    <span>{estimateLoading ? 'Refreshing route intelligence...' : 'Smart pickup suggestions'}</span>
                    {renderRouteChips(pickupAutocomplete, 'pickup', 'booking-route-chip booking-route-chip--suggestion')}
                  </div>
                )}
              </label>

              <label className="booking-field booking-field-wide">
                <span>Drop location</span>
                <input
                  name="drop_location"
                  type="text"
                  value={formState.drop_location}
                  onChange={handleChange}
                  placeholder="Drop city, yard, warehouse, or terminal"
                  aria-invalid={Boolean(fieldErrors.drop_location)}
                  required
                />
                {fieldErrors.drop_location && <small className="booking-field-error">{fieldErrors.drop_location}</small>}
                {dropAutocomplete.length > 0 && (
                  <div className="booking-autocomplete-panel" aria-label="Drop location suggestions">
                    <span>{estimateLoading ? 'Refreshing route intelligence...' : 'Smart drop suggestions'}</span>
                    {renderRouteChips(dropAutocomplete, 'drop', 'booking-route-chip booking-route-chip--suggestion')}
                  </div>
                )}
              </label>

              <label className="booking-field">
                <span>Truck type</span>
                <select name="truck_type" value={formState.truck_type} onChange={handleChange} required>
                  {truckTypes.map((truck) => (
                    <option key={truck.value} value={truck.value}>
                      {truck.label} - {truck.maxLoad}
                    </option>
                  ))}
                </select>
              </label>

              <label className="booking-field">
                <span>Load weight (tons)</span>
                <input
                  name="load_weight"
                  type="number"
                  step="0.1"
                  min="0.1"
                  value={formState.load_weight}
                  onChange={handleChange}
                  placeholder="Enter load weight"
                  aria-invalid={Boolean(fieldErrors.load_weight)}
                  required
                />
                {fieldErrors.load_weight && <small className="booking-field-error">{fieldErrors.load_weight}</small>}
              </label>
            </div>

            {(recentSearchRoutes.length > 0 || popularRoutes.length > 0 || frequentRoutes.length > 0 || personalizedRoutes.length > 0 || routeSearchError) && (
              <section className="booking-route-intelligence-card" aria-labelledby="route-intelligence-title">
                <div className="booking-route-intelligence-header">
                  <div>
                    <p className="booking-form-label">Route intelligence</p>
                    <h3 id="route-intelligence-title">Smart freight suggestions</h3>
                  </div>
                  <span>{estimateLoading ? 'Refreshing...' : 'Live from shipment history'}</span>
                </div>

                <p className="booking-route-intelligence-copy">
                  Recent searches, popular shipment routes, and frequently booked lanes are ranked from live records to help you book faster.
                </p>

                {routeSearchError && <div className="booking-alert booking-alert-error">{routeSearchError}</div>}

                <div className="booking-route-intelligence-grid">
                  <article>
                    <strong>Recent searches</strong>
                    {renderRouteChips(recentSearchRoutes, 'route')}
                  </article>

                  <article>
                    <strong>Popular routes</strong>
                    {renderRouteChips(popularRoutes, 'route')}
                  </article>

                  <article>
                    <strong>Frequently booked</strong>
                    {renderRouteChips(frequentRoutes, 'route')}
                  </article>

                  <article>
                    <strong>Personalized for you</strong>
                    {renderRouteChips(personalizedRoutes, 'route')}
                  </article>

                  {personalizedRecommendations && personalizedRecommendations.length > 0 && (
                    <article>
                      <strong>Recommended routes</strong>
                      {renderRouteChips(personalizedRecommendations.map(r => ({ label: r.label, pickup_location: r.value, drop_location: '' })), 'route')}
                    </article>
                  )}
                </div>

                {recommendedRoutes.length > 0 && (
                  <div className="booking-route-intelligence-footer">
                    <span>{recommendedRoutes.length} live route matches found for the current search.</span>
                  </div>
                )}
              </section>
            )}

            <section className="booking-notification-card" aria-labelledby="notification-settings-title">
              <div className="booking-notification-header">
                <div>
                  <p className="booking-form-label">Notification settings</p>
                  <h3 id="notification-settings-title">WhatsApp updates</h3>
                </div>
                <span className={`booking-notification-pill ${notificationSettings.whatsapp_notifications_enabled ? 'is-on' : 'is-off'}`}>
                  {notificationSettings.whatsapp_notifications_enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>

              <p className="booking-notification-copy">
                Turn on the updates you want to receive. Shipment and payment confirmations are sent automatically when enabled.
              </p>

              <div className="booking-notification-options">
                <label>
                  <input
                    type="checkbox"
                    checked={notificationSettings.whatsapp_notifications_enabled}
                    onChange={() => handleNotificationToggle('whatsapp_notifications_enabled')}
                  />
                  Enable WhatsApp notifications
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={notificationSettings.whatsapp_booking_confirmation}
                    onChange={() => handleNotificationToggle('whatsapp_booking_confirmation')}
                  />
                  Shipment confirmation
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={notificationSettings.whatsapp_payment_confirmation}
                    onChange={() => handleNotificationToggle('whatsapp_payment_confirmation')}
                  />
                  Payment confirmation
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={notificationSettings.whatsapp_live_location}
                    onChange={() => handleNotificationToggle('whatsapp_live_location')}
                  />
                  Live truck location
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={notificationSettings.whatsapp_delivery_updates}
                    onChange={() => handleNotificationToggle('whatsapp_delivery_updates')}
                  />
                  Delivery updates
                </label>
              </div>
            </section>

            <div className="booking-price-note">
              <span>{estimateLoading ? 'Calculating live fare estimate...' : liveEstimate?.reply || 'Live fare estimate updates as you fill the form.'}</span>
              <strong>{selectedTruck.maxLoad}</strong>
              {liveEstimate && (
                <small className="booking-field-hint">
                  Distance {liveEstimate.distance ?? 'pending'} km · ETA {liveEstimate.eta_hours ?? 'pending'} hrs · Fare {formatPrice(liveEstimate.estimated_fare)}
                </small>
              )}
            </div>

            {recommendationError && (
              <div className="booking-alert booking-alert-error" role="alert">
                {recommendationError}
              </div>
            )}

            {vehicleRecommendation?.recommended_vehicle && (
              <section className="booking-recommendation-card" aria-label="Vehicle recommendation">
                <div className="booking-recommendation-header">
                  <div>
                    <p className="booking-form-label">Recommended vehicle</p>
                    <h3>{vehicleRecommendation.recommended_vehicle.vehicle_name}</h3>
                  </div>
                  <span className="booking-status-pill booking-status-pill--subtle">
                    {vehicleRecommendation.recommended_vehicle.availability_status}
                  </span>
                </div>
                <p>{vehicleRecommendation.reasoning}</p>
                <div className="booking-recommendation-meta">
                  <span>{vehicleRecommendation.recommended_vehicle.truck_type}</span>
                  <span>Capacity {vehicleRecommendation.recommended_vehicle.max_load_tons} tons</span>
                  <span>{vehicleRecommendation.recommended_vehicle.vehicle_code}</span>
                </div>
              </section>
            )}

            {bookingResult && (
              <div className="booking-advance-card">
                <div>
                  <span>Advance amount</span>
                  <strong>₹{getAdvanceAmount().toLocaleString('en-IN')}</strong>
                </div>
                <div className="booking-payment-actions">
                  <button type="button" className="booking-pay-button" onClick={handlePayAdvance} disabled={paymentLoading}>
                    {paymentLoading ? 'Opening Razorpay...' : 'Pay Advance'}
                  </button>
                  <button type="button" className="booking-pay-button booking-pay-button--secondary" onClick={handlePayFull} disabled={paymentLoading}>
                    {paymentLoading ? 'Opening Razorpay...' : 'Pay Full'}
                  </button>
                </div>
              </div>
            )}

            {error && (
              <div className="booking-alert booking-alert-error" role="alert">
                {error}
              </div>
            )}

            {phoneError && (
              <div className="booking-alert booking-alert-error" role="alert">
                {phoneError}
              </div>
            )}

            {paymentError && (
              <div className="booking-alert booking-alert-error" role="alert">
                {paymentError}
              </div>
            )}

            <button type="submit" className="booking-submit" disabled={loading}>
              {loading ? 'Creating shipment...' : 'Create shipment'}
            </button>
          </form>
        </div>
      </section>

      <section className="booking-history-section">
        <div className="booking-history-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Shipment history</p>
              <h2>Recent freight orders</h2>
            </div>
            <span>{historyLoading ? 'Syncing...' : `${bookingHistory.length} records`}</span>
          </div>

          {historyError && <div className="booking-alert booking-alert-error">{historyError}</div>}

          <div className="booking-history-list">
            {historyLoading && historyPreview.length === 0 ? (
              Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="booking-history-item booking-history-item--skeleton">
                  <div className="booking-skeleton-line booking-skeleton-line--title" />
                  <div className="booking-skeleton-line" />
                </div>
              ))
            ) : historyPreview.length === 0 ? (
              <EmptyState
                title="No shipments yet"
                description="Create the first shipment to populate the history, payment ledger, and timeline panels with live records."
                accent="amber"
              />
            ) : (
              historyPreview.map((booking) => (
                <button
                  key={booking.id}
                  type="button"
                  className={`booking-history-item ${selectedHistoryBookingId === booking.id ? 'is-selected' : ''}`}
                  onClick={() => setSelectedHistoryBookingId(booking.id)}
                >
                  <div>
                    <strong>{booking.booking_reference || `Shipment #${booking.id}`}</strong>
                    <p>{booking.pickup_location} → {booking.drop_location}</p>
                  </div>
                    <span>{booking.shipment_status || booking.booking_status}</span>
                </button>
              ))
            )}
          </div>
        </div>

        <div className="booking-timeline-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Tracking timeline</p>
              <h2>Live status updates</h2>
            </div>
            <span>{timelineLoading ? 'Loading...' : `${bookingTimeline.length} events`}</span>
          </div>

          <div className="booking-timeline-list">
            {timelineLoading && bookingTimeline.length === 0 ? (
              Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="booking-timeline-item booking-timeline-item--skeleton">
                  <div className="booking-timeline-dot booking-timeline-dot--skeleton" />
                  <div className="booking-skeleton-block">
                    <div className="booking-skeleton-line booking-skeleton-line--title" />
                    <div className="booking-skeleton-line" />
                  </div>
                </div>
              ))
            ) : bookingTimeline.length === 0 ? (
              <EmptyState
                title="Timeline is waiting"
                description="Select a shipment or create a new one to reveal the operational timeline and shipment progress."
                accent="blue"
              />
            ) : (
              bookingTimeline.map((entry) => (
                <article key={`${entry.id}-${entry.created_at}`} className="booking-timeline-item">
                  <div className="booking-timeline-dot" />
                  <div>
                    <strong>{entry.status}</strong>
                    <p>{entry.note || 'Status update recorded by the shipment engine.'}</p>
                    <span>{entry.location || 'Logistics control tower'} · {entry.created_at}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>

        <div className="booking-history-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Activity center</p>
              <h2>Journey events</h2>
            </div>
            <span>{eventsLoading ? 'Loading...' : `${(activityFeed && activityFeed.length) || shipmentEvents.length} events`}</span>
          </div>

          {eventsError && <div className="booking-alert booking-alert-error">{eventsError}</div>}

          <div className="booking-activity-summary">
            {activitySummary && Object.keys(activitySummary).length > 0 && (
              <div className="booking-activity-counters">
                {Object.entries(activitySummary).slice(0, 6).map(([k, v]) => (
                  <div key={k} className="booking-activity-counter">
                    <strong>{v}</strong>
                    <span>{k.replace(/_/g, ' ')}</span>
                  </div>
                ))}
              </div>
            )}

            {activeShipments && activeShipments.length > 0 && (
              <div className="booking-active-shipments">
                <strong>Active shipments</strong>
                <div className="booking-active-list">
                  {activeShipments.slice(0, 4).map((s) => (
                    <button key={s.id || s.shipment_id} type="button" className="booking-active-item" onClick={() => setSelectedHistoryBookingId(s.id || s.shipment_id)}>
                      <div>
                        <strong>{s.booking_reference || `#${s.id || s.shipment_id}`}</strong>
                        <p>{s.eta_phrase || `${s.pickup_location || ''} → ${s.drop_location || ''}`}</p>
                      </div>
                      <span>{s.driver_reference || s.driver_name || s.shipment_status}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {recentShipments && recentShipments.length > 0 && (
              <div className="booking-recent-shipments">
                <strong>Recent</strong>
                <div className="booking-recent-list">
                  {recentShipments.slice(0, 6).map((r) => (
                    <button key={r.id || r.shipment_id} type="button" className="booking-recent-item" onClick={() => setSelectedHistoryBookingId(r.id || r.shipment_id)}>
                      <div>
                        <strong>{r.booking_reference || `#${r.id || r.shipment_id}`}</strong>
                        <p>{r.eta_phrase || `${r.pickup_location || ''} → ${r.drop_location || ''}`}</p>
                      </div>
                      <span>{r.shipment_status || r.payment_status || ''}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="booking-history-list">
            {eventsLoading && (activityFeed.length === 0 && shipmentEvents.length === 0) ? (
              Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="booking-history-item booking-history-item--skeleton">
                  <div className="booking-skeleton-line booking-skeleton-line--title" />
                  <div className="booking-skeleton-line" />
                </div>
              ))
            ) : (activityFeed.length > 0 ? (
              activityFeed.slice(0, 12).map((item) => (
                <article key={`${item.id || item.event_id}-${item.timestamp || item.created_at || ''}`} className="booking-history-item booking-activity-item">
                  <div>
                    <strong>{item.title || item.summary || item.message || item.type}</strong>
                    <p>{item.message || item.summary || item.detail || ''}</p>
                    <small className="booking-activity-meta">
                      {item.shipment_reference && <span>{item.shipment_reference}</span>}
                      {item.driver_reference && <span> · {item.driver_reference}</span>}
                      {item.eta_phrase && <span> · ETA {item.eta_phrase}</span>}
                    </small>
                  </div>
                  <div className="booking-payment-history-meta">
                    <span>{item.severity || item.category || 'info'}</span>
                    <span>{item.timestamp || item.created_at || ''}</span>
                    {item.actions && Array.isArray(item.actions) && (
                      <div className="booking-activity-actions">
                        {item.actions.map((action, idx) => (
                          <button
                            key={idx}
                            type="button"
                            className="booking-activity-action"
                            onClick={() => {
                              if (action.type === 'open_url' && (action.url || action.value)) {
                                window.open(action.url || action.value, '_blank', 'noopener,noreferrer')
                              } else if (action.type === 'open_invoice' && action.url) {
                                window.open(action.url, '_blank', 'noopener,noreferrer')
                              } else if (action.url) {
                                window.open(action.url, '_blank', 'noopener,noreferrer')
                              }
                            }}
                          >
                            {action.label || action.title || action.type}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              ))
            ) : (
              shipmentEvents.slice(0, 8).map((event) => (
                <article key={`${event.id}-${event.created_at}`} className="booking-history-item booking-activity-item">
                  <div>
                    <strong>{event.title || event.event_type}</strong>
                    <p>{event.message || 'Journey update recorded.'}</p>
                  </div>
                  <div className="booking-payment-history-meta">
                    <span>{event.severity || 'info'}</span>
                    <span>{event.source || 'system'}</span>
                  </div>
                </article>
              ))
            ))}
          </div>
        </div>

        <div className="booking-history-card">
          <div className="booking-history-header">
            <div>
              <p className="booking-form-label">Payment history</p>
              <h2>Razorpay ledger</h2>
            </div>
            <span>{paymentHistoryLoading ? 'Loading...' : `${paymentHistory.length} records`}</span>
          </div>

          {paymentHistoryError && <div className="booking-alert booking-alert-error">{paymentHistoryError}</div>}

          <div className="booking-history-list booking-payment-history-list">
            {paymentHistoryLoading && paymentHistory.length === 0 ? (
              Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="booking-history-item booking-payment-history-item booking-payment-history-item--skeleton">
                  <div className="booking-skeleton-line booking-skeleton-line--title" />
                  <div className="booking-skeleton-line" />
                </div>
              ))
            ) : paymentHistory.length === 0 ? (
              <EmptyState
                title="No payments captured yet"
                description="Create a shipment to inspect the payment flow and invoice journey from real backend records."
                accent="green"
              />
            ) : (
              paymentHistory.map((payment) => (
                <article key={`${payment.id}-${payment.razorpay_order_id}`} className="booking-history-item booking-payment-history-item">
                  <div>
                    <strong>{payment.payment_type || 'advance'} payment</strong>
                    <p>{payment.razorpay_order_id || 'Pending order reference'}</p>
                  </div>
                  <div className="booking-payment-history-meta">
                    <span>{formatPrice(payment.amount)}</span>
                    <span>{payment.payment_status || 'created'}</span>
                  </div>
                </article>
              ))
            )}
          </div>
        </div>
      </section>

      {bookingResult && (
        <div className="booking-modal-backdrop" role="presentation" onClick={closeSuccessModal}>
          <div
            className="booking-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="booking-success-title"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="booking-modal-kicker">Shipment created</p>
            <h2 id="booking-success-title">{bookingResult.booking_reference || bookingReference || `Shipment #${bookingResult.id}`}</h2>
            <p className="booking-modal-copy">
              Your shipment has been saved successfully. The order is ready for dispatch planning.
            </p>

            <div className="booking-modal-grid">
              <div>
                <span>Customer</span>
                <strong>{bookingResult.customer_name || formState.customer_name}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{bookingResult.shipment_status || 'pending'}</strong>
              </div>
              <div>
                <span>Truck</span>
                <strong>{bookingResult.truck_type || selectedTruck.label}</strong>
              </div>
              <div>
                <span>Price</span>
                <strong>{formatPrice(bookingResult.estimated_price ?? bookingResult.price)}</strong>
              </div>
              <div>
                <span>Advance</span>
                <strong>{formatPrice(getAdvanceAmount())}</strong>
              </div>
              <div>
                <span>Balance</span>
                <strong>{formatPrice(Number(bookingResult.estimated_price ?? bookingResult.price ?? 0) - getAdvanceAmount())}</strong>
              </div>
              <div>
                <span>Payment status</span>
                <strong>{bookingResult.payment_status || 'pending'}</strong>
              </div>
            </div>

            <div className="booking-modal-route">
              <span>{bookingResult.pickup_location || formState.pickup_location}</span>
              <span>to</span>
              <span>{bookingResult.drop_location || formState.drop_location}</span>
            </div>

            <Link
              to={`/booking-confirmation/${bookingResult.id}`}
              state={{ booking: bookingResult, vehicleRecommendation, routeCoordinates: liveEstimate?.route_coordinates || [] }}
              className="booking-pay-button booking-pay-button-modal booking-confirmation-link"
            >
              Open confirmation page
            </Link>

            <div className="booking-payment-actions booking-payment-actions--modal">
              <button type="button" className="booking-pay-button booking-pay-button-modal" onClick={handlePayAdvance} disabled={paymentLoading}>
                {paymentLoading ? 'Opening Razorpay...' : 'Pay Advance'}
              </button>
              <button type="button" className="booking-pay-button booking-pay-button-modal booking-pay-button--secondary" onClick={handlePayFull} disabled={paymentLoading}>
                {paymentLoading ? 'Opening Razorpay...' : 'Pay Full'}
              </button>
            </div>

            <button type="button" className="booking-pay-button booking-pay-button-modal booking-pay-button--secondary" onClick={downloadInvoice}>
              Download GST Invoice
            </button>

            <button type="button" className="booking-modal-close" onClick={closeSuccessModal}>
              Close
            </button>
          </div>
        </div>
      )}

      {paymentSuccess && (
        <div className="booking-modal-backdrop payment-success-backdrop" role="presentation" onClick={closePaymentSuccessModal}>
          <div
            className="booking-modal payment-success-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="payment-success-title"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="booking-modal-kicker">Payment verified</p>
            <h2 id="payment-success-title">{paymentSuccess.paymentType === 'full' ? 'Full payment successful' : 'Advance payment successful'}</h2>
            <p className="booking-modal-copy">
              Your payment has been captured and the booking status has been updated automatically.
            </p>

            <div className="booking-modal-grid">
              <div>
                <span>Payment ID</span>
                <strong>{paymentSuccess.paymentId}</strong>
              </div>
              <div>
                <span>Order ID</span>
                <strong>{paymentSuccess.orderId}</strong>
              </div>
              <div>
                <span>Booking ID</span>
                <strong>#{paymentSuccess.bookingId}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{paymentSuccess.bookingStatus}</strong>
              </div>
              <div>
                <span>Type</span>
                <strong>{paymentSuccess.paymentType}</strong>
              </div>
            </div>

            <div className="booking-payment-actions booking-payment-actions--modal">
              <button type="button" className="booking-pay-button booking-pay-button-modal booking-pay-button--secondary" onClick={downloadInvoice}>
                Download GST Invoice
              </button>
              {paymentSuccess.paymentType !== 'full' && (
                <button type="button" className="booking-pay-button booking-pay-button-modal" onClick={handlePayFull} disabled={paymentLoading}>
                  {paymentLoading ? 'Opening Razorpay...' : 'Pay Full Balance'}
                </button>
              )}
            </div>

            <button type="button" className="booking-modal-close" onClick={closePaymentSuccessModal}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
