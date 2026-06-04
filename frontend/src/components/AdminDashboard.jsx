import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  createAdminVehicle,
  fetchAdminAiMetrics,
  fetchAdminAiActionLogs,
  fetchAdminAiWorkflows,
  fetchAdminControlTower,
  fetchAdminAuditLogs,
  fetchAdminDashboard,
  fetchAdminMonitoring,
  fetchAdminShipments,
  fetchAdminVehicles,
  fetchAdminWebhookEvents,
  deleteAdminAuditLogs,
  deleteAdminVehicle,
  retryAdminWebhookEvent,
  updateAdminDriver,
  updateAdminVehicle,
  updateAdminShipmentStatus,
  updateAdminTruck,
} from '../services/adminService'
import { EmptyState, PageSkeleton } from './AppChrome'
import '../styles/AdminDashboard.css'

const sidebarLinks = [
  { id: 'overview', label: 'Overview' },
  { id: 'control-tower', label: 'Control Tower' },
  { id: 'monitoring', label: 'Monitoring' },
  { id: 'ai-observability', label: 'AI Observability' },
  { id: 'bookings', label: 'Bookings' },
  { id: 'shipments', label: 'Shipments' },
  { id: 'drivers', label: 'Drivers' },
  { id: 'trucks', label: 'Trucks' },
  { id: 'vehicles', label: 'Vehicles' },
  { id: 'audit', label: 'Audit Logs' },
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'payments', label: 'Payments' },
]

const bookingStatusOptions = ['all', 'pending', 'confirmed', 'assigned', 'loading', 'in_transit', 'completed', 'cancelled']
const paymentStatusOptions = ['all', 'created', 'pending', 'paid', 'failed', 'refunded']
const shipmentStatusOptions = ['pending', 'confirmed', 'assigned', 'in_transit', 'delivered']
const driverStatusOptions = ['available', 'on_trip', 'maintenance', 'on_leave']
const truckStatusOptions = ['available', 'on_trip', 'maintenance', 'offline']

const revenueColors = ['#f97316', '#38bdf8', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']
const paymentColors = ['#f97316', '#38bdf8', '#22c55e', '#f59e0b', '#ef4444']

function formatCurrency(value) {
  const numeric = Number(value || 0)
  return `₹${numeric.toLocaleString('en-IN')}`
}

function formatLatency(value) {
  const numeric = Number(value || 0)
  if (!Number.isFinite(numeric)) {
    return '0 ms'
  }

  if (numeric >= 1000) {
    return `${(numeric / 1000).toFixed(2)} s`
  }

  return `${numeric.toFixed(numeric >= 100 ? 0 : 1)} ms`
}

function formatUptime(seconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(seconds || 0)))
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const remainder = totalSeconds % 60

  const parts = []
  if (days) parts.push(`${days}d`)
  if (hours || parts.length) parts.push(`${hours}h`)
  if (minutes || parts.length) parts.push(`${minutes}m`)
  parts.push(`${remainder}s`)
  return parts.join(' ')
}

function formatDate(value) {
  if (!value) {
    return 'Pending'
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return String(value)
  }

  return parsed.toLocaleString()
}

function getTruckTypeCode(truckType) {
  const code = String(truckType || '').trim().split(' ')[0]
  return ['12', '14', '16'].includes(code) ? code : '12'
}

function MetricCard({ label, value, hint, tone }) {
  return (
    <article className={`admin-metric-card ${tone || ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <p>{hint}</p>}
    </article>
  )
}

function ChartCard({ title, subtitle, children, className = '' }) {
  return (
    <section className={`admin-chart-card ${className}`}>
      <div className="admin-card-heading">
        <div>
          <p className="admin-kicker">Analytics</p>
          <h3>{title}</h3>
        </div>
        {subtitle && <span>{subtitle}</span>}
      </div>
      {children}
    </section>
  )
}

function Badge({ children, tone = 'neutral' }) {
  return <span className={`admin-badge ${tone}`}>{children}</span>
}

export default function AdminDashboard() {
  const [dashboard, setDashboard] = useState({
    summary: {},
    bookings: [],
    active_shipments: [],
    shipments: [],
    drivers: [],
    trucks: [],
    analytics: {
      revenue_series: [],
      booking_status_series: [],
      payment_status_series: [],
    },
    payment_records: [],
    ai_metrics: {
      tool_metrics: [],
      workflows: [],
      action_logs: [],
      summary: {},
    },
    vehicles: [],
    audit_logs: [],
    webhook_events: [],
    monitoring: {
      summary: {},
      method_series: [],
      status_class_series: [],
      route_series: [],
      latency_series: [],
      recent_requests: [],
      recent_errors: [],
      log_errors: [],
      health: {},
    },
    control_tower: {
      operational_alerts: [],
      payments: { summary: {}, anomalies: [], wallet_ledger: [] },
      driver_utilization: { summary: {} },
      sla: { summary: {} },
      retry_queue: [],
      webhook_monitoring: { items: [] },
      audit_logs: { items: [] },
      ai_observability: { summary: {} },
    },
  })
  const [bookingStatus, setBookingStatus] = useState('all')
  const [paymentStatus, setPaymentStatus] = useState('all')
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)
  const [savingDriverId, setSavingDriverId] = useState(null)
  const [savingTruckKey, setSavingTruckKey] = useState(null)
  const [savingShipmentId, setSavingShipmentId] = useState(null)
  const [savingVehicleCode, setSavingVehicleCode] = useState(null)
  const [creatingVehicle, setCreatingVehicle] = useState(false)
  const [purgingAuditLogs, setPurgingAuditLogs] = useState(false)
  const [retryingWebhookId, setRetryingWebhookId] = useState(null)
  const [error, setError] = useState('')
  const [monitoringIssue, setMonitoringIssue] = useState('')
  const [activeSection, setActiveSection] = useState('overview')
  const sectionRefs = useRef({})
  const [driverDrafts, setDriverDrafts] = useState({})
  const [truckDrafts, setTruckDrafts] = useState({})
  const [shipmentDrafts, setShipmentDrafts] = useState({})
  const [vehicleDrafts, setVehicleDrafts] = useState({})
  const [newVehicleDraft, setNewVehicleDraft] = useState({
    vehicle_code: '',
    vehicle_name: '',
    truck_type: '12 tyre',
    max_load_tons: '',
    rate_per_km: '',
    availability_status: 'available',
  })

  const loadDashboard = async () => {
    setLoading(true)
    setError('')
    setMonitoringIssue('')

    try {
      const [dashboardResult, shipmentResult, monitoringResult, controlTowerResult, aiMetricsResult, actionLogsResult, workflowsResult, auditResult, webhookResult, vehiclesResult] = await Promise.allSettled([
        fetchAdminDashboard({ bookingStatus, paymentStatus, days }),
        fetchAdminShipments(),
        fetchAdminMonitoring({ days }),
        fetchAdminControlTower({ days }),
        fetchAdminAiMetrics(),
        fetchAdminAiActionLogs({ perPage: 20 }),
        fetchAdminAiWorkflows({}),
        fetchAdminAuditLogs({ perPage: 20 }),
        fetchAdminWebhookEvents({ perPage: 20 }),
        fetchAdminVehicles(),
      ])

      if (dashboardResult.status === 'rejected') {
        throw dashboardResult.reason
      }

      const dashboardData = dashboardResult.value || {}
      const shipmentData = shipmentResult.status === 'fulfilled' ? shipmentResult.value : { shipments: [] }
      const controlTowerData = controlTowerResult.status === 'fulfilled' ? controlTowerResult.value : {}
      const aiMetricsData = aiMetricsResult.status === 'fulfilled' ? aiMetricsResult.value : {}
      const actionLogsData = actionLogsResult.status === 'fulfilled' ? actionLogsResult.value : { items: [] }
      const workflowsData = workflowsResult.status === 'fulfilled' ? workflowsResult.value : { workflows: [] }
      const auditData = auditResult.status === 'fulfilled' ? auditResult.value : { logs: [] }
      const webhookData = webhookResult.status === 'fulfilled' ? webhookResult.value : { events: [] }
      const vehicleData = vehiclesResult.status === 'fulfilled' ? vehiclesResult.value : { vehicles: [] }

      setDashboard({
        ...dashboardData,
        shipments: Array.isArray(shipmentData.shipments) ? shipmentData.shipments : [],
        ai_metrics: {
          tool_metrics: Array.isArray(aiMetricsData.tools) ? aiMetricsData.tools : Array.isArray(aiMetricsData.tool_metrics) ? aiMetricsData.tool_metrics : [],
          workflows: Array.isArray(workflowsData.workflows) ? workflowsData.workflows : [],
          action_logs: Array.isArray(actionLogsData.items) ? actionLogsData.items : Array.isArray(actionLogsData.action_logs) ? actionLogsData.action_logs : [],
          summary: aiMetricsData.summary || {},
        },
        vehicles: Array.isArray(vehicleData.vehicles) ? vehicleData.vehicles : [],
        audit_logs: Array.isArray(auditData.items) ? auditData.items : Array.isArray(auditData.logs) ? auditData.logs : Array.isArray(auditData.audit_logs) ? auditData.audit_logs : [],
        webhook_events: Array.isArray(webhookData.items) ? webhookData.items : Array.isArray(webhookData.events) ? webhookData.events : Array.isArray(webhookData.webhook_events) ? webhookData.webhook_events : [],
        monitoring: monitoringResult.status === 'fulfilled'
          ? {
              summary: {},
              method_series: [],
              status_class_series: [],
              route_series: [],
              latency_series: [],
              recent_requests: [],
              recent_errors: [],
              log_errors: [],
              health: {},
              ...(monitoringResult.value || {}),
            }
          : dashboard.monitoring,
        control_tower: {
          operational_alerts: [],
          payments: { summary: {}, anomalies: [], wallet_ledger: [] },
          driver_utilization: { summary: {} },
          sla: { summary: {} },
          retry_queue: [],
          webhook_monitoring: { items: [] },
          audit_logs: { items: [] },
          ai_observability: { summary: {} },
          ...(controlTowerData || {}),
        },
      })

      if (monitoringResult.status === 'rejected') {
        setMonitoringIssue(monitoringResult.reason instanceof Error ? monitoringResult.reason.message : 'Unable to load monitoring data')
      }
      if (controlTowerResult.status === 'rejected') {
        setMonitoringIssue(controlTowerResult.reason instanceof Error ? controlTowerResult.reason.message : 'Unable to load control tower data')
      }
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : 'Unable to load admin dashboard')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadDashboard()
  }, [bookingStatus, paymentStatus, days])

  useEffect(() => {
    const nextDriverDrafts = {}
    for (const driver of dashboard.drivers || []) {
      nextDriverDrafts[driver.id] = {
        status: driver.status || 'available',
        assigned_truck: driver.assigned_truck || '',
        assigned_truck_type: driver.assigned_truck_type || '',
      }
    }
    setDriverDrafts(nextDriverDrafts)
  }, [dashboard.drivers])

  useEffect(() => {
    const nextTruckDrafts = {}
    for (const truck of dashboard.trucks || []) {
      nextTruckDrafts[`${truck.truck_type}-${truck.vehicle_number}`] = {
        availability_status: truck.availability_status || 'available',
      }
    }
    setTruckDrafts(nextTruckDrafts)
  }, [dashboard.trucks])

  useEffect(() => {
    const nextShipmentDrafts = {}
    for (const shipment of dashboard.shipments || []) {
      nextShipmentDrafts[shipment.id] = {
        shipment_status: shipment.shipment_status || 'pending',
        assigned_driver_id: shipment.assigned_driver_id ?? '',
      }
    }
    setShipmentDrafts(nextShipmentDrafts)
  }, [dashboard.shipments])

  useEffect(() => {
    const nextVehicleDrafts = {}
    for (const vehicle of dashboard.vehicles || []) {
      nextVehicleDrafts[vehicle.vehicle_code] = {
        vehicle_name: vehicle.vehicle_name || '',
        truck_type: vehicle.truck_type || '',
        max_load_tons: vehicle.max_load_tons ?? '',
        rate_per_km: vehicle.rate_per_km ?? '',
        availability_status: vehicle.availability_status || 'available',
        current_booking_id: vehicle.current_booking_id ?? '',
        ai_notes: vehicle.ai_notes || '',
      }
    }
    setVehicleDrafts(nextVehicleDrafts)
  }, [dashboard.vehicles])

  const sections = useMemo(() => sidebarLinks, [])
  const revenueSeries = dashboard.analytics?.revenue_series || []
  const bookingStatusSeries = dashboard.analytics?.booking_status_series || []
  const paymentStatusSeries = dashboard.analytics?.payment_status_series || []
  const monitoring = dashboard.monitoring || {}
  const monitoringSummary = monitoring.summary || {}
  const monitoringMethodSeries = monitoring.method_series || []
  const monitoringStatusSeries = monitoring.status_class_series || []
  const monitoringRouteSeries = monitoring.route_series || []
  const monitoringLatencySeries = monitoring.latency_series || []
  const monitoringRecentRequests = monitoring.recent_requests || []
  const monitoringRecentErrors = monitoring.recent_errors || []
  const monitoringLogErrors = monitoring.log_errors || []
  const monitoringHealth = monitoring.health || {}
  const controlTower = dashboard.control_tower || {}
  const controlPayments = controlTower.payments || {}
  const controlPaymentSummary = controlPayments.summary || {}
  const controlAlerts = Array.isArray(controlTower.operational_alerts) ? controlTower.operational_alerts : []
  const controlAnomalies = Array.isArray(controlPayments.anomalies) ? controlPayments.anomalies : []
  const controlWalletLedger = Array.isArray(controlPayments.wallet_ledger) ? controlPayments.wallet_ledger : []
  const controlRetryQueue = Array.isArray(controlTower.retry_queue) ? controlTower.retry_queue : []
  const controlDriverUtilization = controlTower.driver_utilization?.summary || {}
  const controlSla = controlTower.sla?.summary || {}
  const visibleShipments = dashboard.shipments || []
  const visibleBookings = dashboard.bookings || []
  const visibleDrivers = dashboard.drivers || []
  const visiblePayments = dashboard.payment_records || []
  const visibleVehicles = dashboard.vehicles || []
  const visibleAuditLogs = dashboard.audit_logs || []
  const visibleWebhookEvents = dashboard.webhook_events || []
  const aiMetrics = dashboard.ai_metrics || {}
  const aiToolMetrics = aiMetrics.tool_metrics || []
  const aiWorkflows = aiMetrics.workflows || []
  const aiActionLogs = aiMetrics.action_logs || []
  const shipmentStatusCounts = useMemo(() => {
    const counts = { pending: 0, confirmed: 0, assigned: 0, in_transit: 0, delivered: 0 }
    for (const shipment of visibleShipments || []) {
      const status = String(shipment.shipment_status || 'pending').toLowerCase()
      if (status in counts) {
        counts[status] += 1
      } else {
        counts.pending += 1
      }
    }
    return counts
  }, [visibleShipments])

  if (loading && !dashboard.shipments.length && !dashboard.drivers.length && !dashboard.trucks.length) {
    return <PageSkeleton title="Loading admin control tower" copy="Synchronizing shipments, drivers, trucks, payments, and analytics..." rows={5} />
  }

  const handleSectionJump = (sectionId) => {
    setActiveSection(sectionId)
    const section = sectionRefs.current[sectionId]
    section?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const updateDriverDraft = (driverId, field, value) => {
    setDriverDrafts((previous) => ({
      ...previous,
      [driverId]: {
        ...(previous[driverId] || {}),
        [field]: value,
      },
    }))
  }

  const updateTruckDraft = (truckKey, value) => {
    setTruckDrafts((previous) => ({
      ...previous,
      [truckKey]: {
        ...(previous[truckKey] || {}),
        availability_status: value,
      },
    }))
  }

  const updateShipmentDraft = (shipmentId, field, value) => {
    setShipmentDrafts((previous) => ({
      ...previous,
      [shipmentId]: {
        ...(previous[shipmentId] || {}),
        [field]: value,
      },
    }))
  }

  const updateVehicleDraft = (vehicleCode, field, value) => {
    setVehicleDrafts((previous) => ({
      ...previous,
      [vehicleCode]: {
        ...(previous[vehicleCode] || {}),
        [field]: value,
      },
    }))
  }

  const saveDriver = async (driver) => {
    const draft = driverDrafts[driver.id] || {}
    setSavingDriverId(driver.id)
    setError('')

    try {
      await updateAdminDriver(driver.id, {
        status: draft.status || driver.status,
        assigned_truck: draft.assigned_truck ?? driver.assigned_truck,
        assigned_truck_type: draft.assigned_truck_type ?? driver.assigned_truck_type,
      })
      await loadDashboard()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save driver changes')
    } finally {
      setSavingDriverId(null)
    }
  }

  const saveTruck = async (truck) => {
    const truckKey = `${truck.truck_type}-${truck.vehicle_number}`
    const draft = truckDrafts[truckKey] || {}
    setSavingTruckKey(truckKey)
    setError('')

    try {
      await updateAdminTruck(getTruckTypeCode(truck.truck_type), truck.vehicle_number, {
        availability_status: draft.availability_status || truck.availability_status,
      })
      await loadDashboard()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save truck changes')
    } finally {
      setSavingTruckKey(null)
    }
  }

  const saveShipment = async (shipment) => {
    const draft = shipmentDrafts[shipment.id] || {}
    setSavingShipmentId(shipment.id)
    setError('')

    const nextDriverId = draft.assigned_driver_id === '' || draft.assigned_driver_id === null || draft.assigned_driver_id === undefined
      ? null
      : Number(draft.assigned_driver_id)

    try {
      await updateAdminShipmentStatus(shipment.id, {
        shipment_status: draft.shipment_status || shipment.shipment_status,
        assigned_driver_id: Number.isFinite(nextDriverId) ? nextDriverId : null,
        note: 'Updated from admin dashboard',
      })
      await loadDashboard()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save shipment changes')
    } finally {
      setSavingShipmentId(null)
    }
  }

  const saveVehicle = async (vehicle) => {
    const draft = vehicleDrafts[vehicle.vehicle_code] || {}
    setSavingVehicleCode(vehicle.vehicle_code)
    setError('')

    try {
      await updateAdminVehicle(vehicle.vehicle_code, {
        vehicle_name: draft.vehicle_name || vehicle.vehicle_name,
        truck_type: draft.truck_type || vehicle.truck_type,
        max_load_tons: draft.max_load_tons === '' ? vehicle.max_load_tons : draft.max_load_tons,
        rate_per_km: draft.rate_per_km === '' ? vehicle.rate_per_km : draft.rate_per_km,
        availability_status: draft.availability_status || vehicle.availability_status,
        current_booking_id: draft.current_booking_id === '' ? null : draft.current_booking_id,
        ai_notes: draft.ai_notes ?? vehicle.ai_notes,
      })
      await loadDashboard()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unable to save vehicle changes')
    } finally {
      setSavingVehicleCode(null)
    }
  }

  const createVehicle = async () => {
    setCreatingVehicle(true)
    setError('')

    try {
      await createAdminVehicle({
        vehicle_code: newVehicleDraft.vehicle_code,
        vehicle_name: newVehicleDraft.vehicle_name,
        truck_type: newVehicleDraft.truck_type,
        max_load_tons: newVehicleDraft.max_load_tons,
        rate_per_km: newVehicleDraft.rate_per_km,
        availability_status: newVehicleDraft.availability_status,
      })
      setNewVehicleDraft({
        vehicle_code: '',
        vehicle_name: '',
        truck_type: '12 tyre',
        max_load_tons: '',
        rate_per_km: '',
        availability_status: 'available',
      })
      await loadDashboard()
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : 'Unable to create vehicle')
    } finally {
      setCreatingVehicle(false)
    }
  }

  const purgeAuditLogs = async () => {
    setPurgingAuditLogs(true)
    setError('')

    try {
      await deleteAdminAuditLogs({ olderThanDays: 30 })
      await loadDashboard()
    } catch (purgeError) {
      setError(purgeError instanceof Error ? purgeError.message : 'Unable to purge audit logs')
    } finally {
      setPurgingAuditLogs(false)
    }
  }

  const retryWebhookEvent = async (eventId) => {
    setRetryingWebhookId(eventId)
    setError('')

    try {
      await retryAdminWebhookEvent(eventId, { status: 'retrying' })
      await loadDashboard()
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : 'Unable to retry webhook event')
    } finally {
      setRetryingWebhookId(null)
    }
  }

  return (
    <div className="admin-dashboard">
      <aside className="admin-sidebar">
        <div className="admin-brand">
          <p>SKDLS Transport</p>
          <h1>Admin Control Tower</h1>
          <span>Operations, revenue, fleet and shipment oversight</span>
        </div>

        <nav className="admin-nav" aria-label="Admin dashboard sections">
          {sections.map((section) => (
            <button
              key={section.id}
              type="button"
              className={`admin-nav-link ${activeSection === section.id ? 'active' : ''}`}
              onClick={() => handleSectionJump(section.id)}
            >
              {section.label}
            </button>
          ))}
        </nav>

        <div className="admin-sidebar-card">
          <p>Live filters</p>
          <label>
            Booking status
            <select value={bookingStatus} onChange={(event) => setBookingStatus(event.target.value)}>
              {bookingStatusOptions.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>
          <label>
            Payment status
            <select value={paymentStatus} onChange={(event) => setPaymentStatus(event.target.value)}>
              {paymentStatusOptions.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>
          <label>
            Days
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {[7, 14, 30, 60, 90].map((dayValue) => (
                <option key={dayValue} value={dayValue}>
                  Last {dayValue} days
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="admin-refresh-button" onClick={loadDashboard} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh data'}
          </button>
        </div>
      </aside>

      <main className="admin-content">
        <header className="admin-hero" ref={(node) => { sectionRefs.current.overview = node }}>
          <div>
            <p className="admin-kicker">Dark modern logistics control tower</p>
            <h2>Professional transport admin dashboard</h2>
            <p>
              Track every booking, revenue stream, shipment, driver, and truck from one operational cockpit.
            </p>
          </div>
          <div className="admin-hero-meta">
            <Badge tone="orange">{dashboard.summary?.active_shipments ?? 0} active shipments</Badge>
            <Badge tone="blue">{dashboard.summary?.live_trucks ?? 0} live trucks</Badge>
            <Badge tone="green">{dashboard.summary?.drivers_on_duty ?? 0} drivers on duty</Badge>
          </div>
        </header>

        {error && <div className="admin-alert">{error}</div>}

        <section className="admin-panel" ref={(node) => { sectionRefs.current['control-tower'] = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Enterprise control tower</p>
              <h3>Operational alerts, payments, SLA, and retry queues</h3>
            </div>
            <span>{controlAlerts.length} live alerts</span>
          </div>

          <section className="admin-metrics-grid monitoring-metrics-grid">
            <MetricCard label="Payment success" value={`${controlPaymentSummary.success_rate ?? 100}%`} hint={`${controlPaymentSummary.successful_payments ?? 0} successful payments`} tone="green" />
            <MetricCard label="Payment anomalies" value={controlAnomalies.length} hint="Failed or stale payment orders" tone="amber" />
            <MetricCard label="Wallet balance rows" value={controlWalletLedger.length} hint="Customer ledger entries loaded" tone="blue" />
            <MetricCard label="Driver utilization" value={`${controlDriverUtilization.utilization_rate ?? 0}%`} hint={`${controlDriverUtilization.on_trip ?? 0} drivers on trip`} tone="violet" />
            <MetricCard label="SLA breach rate" value={`${controlSla.sla_breach_rate ?? 0}%`} hint={`${controlSla.delayed_shipments ?? 0} delayed shipments`} tone="orange" />
            <MetricCard label="Retry queue" value={controlRetryQueue.length} hint="AI/background jobs awaiting attention" tone="slate" />
          </section>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Real-time operational alerts" subtitle="Payment, SLA, and system exceptions">
              <div className="admin-monitoring-log">
                {controlAlerts.length === 0 ? (
                  <EmptyState title="No operational alerts" description="Payment anomalies, SLA breaches, and system alerts will appear here." accent="green" />
                ) : (
                  controlAlerts.slice(0, 8).map((alert, index) => (
                    <div className="admin-monitoring-log-line" key={`${alert.type || 'alert'}-${index}`}>
                      <strong>{alert.severity || 'info'}</strong>
                      <span>{alert.type || 'operational_alert'}</span>
                      <p>{alert.message || 'Operational alert'}</p>
                    </div>
                  ))
                )}
              </div>
            </ChartCard>

            <ChartCard title="Payment anomaly detection" subtitle="Failed, stale, or refund-sensitive records">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Severity</th>
                      <th>Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {controlAnomalies.length === 0 ? (
                      <tr>
                        <td colSpan="3">
                          <EmptyState title="No payment anomalies" description="The payment monitor has not detected failed or stale orders." accent="green" />
                        </td>
                      </tr>
                    ) : (
                      controlAnomalies.slice(0, 8).map((anomaly, index) => (
                        <tr key={`${anomaly.type || 'anomaly'}-${index}`}>
                          <td>{anomaly.type || 'payment_anomaly'}</td>
                          <td><Badge tone={anomaly.severity === 'critical' ? 'amber' : 'orange'}>{anomaly.severity || 'warning'}</Badge></td>
                          <td>{anomaly.message || 'Payment anomaly detected'}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>
          </div>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Retry queue viewer" subtitle="AI and background recovery queue">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Workflow</th>
                      <th>Status</th>
                      <th>Retries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {controlRetryQueue.length === 0 ? (
                      <tr><td colSpan="4"><EmptyState title="Retry queue is clear" description="Queued workflow recovery jobs will appear here." accent="blue" /></td></tr>
                    ) : (
                      controlRetryQueue.slice(0, 8).map((job) => (
                        <tr key={job.id}>
                          <td>{job.id}</td>
                          <td>{job.workflow_type || job.action || 'workflow'}</td>
                          <td><Badge tone={job.status === 'queued' ? 'orange' : 'slate'}>{job.status || 'unknown'}</Badge></td>
                          <td>{job.retry_count ?? 0}/{job.max_retries ?? 0}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>

            <ChartCard title="Customer wallet ledger" subtitle="Latest ledger entries">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Type</th>
                      <th>Amount</th>
                      <th>Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {controlWalletLedger.length === 0 ? (
                      <tr><td colSpan="4"><EmptyState title="No wallet entries" description="Captured payments and refunds will populate the wallet ledger." accent="slate" /></td></tr>
                    ) : (
                      controlWalletLedger.slice(0, 8).map((entry) => (
                        <tr key={entry.id}>
                          <td>{entry.user_id || 'customer'}</td>
                          <td>{entry.entry_type}</td>
                          <td>{formatCurrency(entry.amount)}</td>
                          <td>{formatCurrency(entry.balance_after)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.monitoring = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Operations center</p>
              <h3>Application monitoring</h3>
            </div>
            <div className="admin-ops-badges">
              <Badge tone={monitoringHealth.database === 'ok' ? 'green' : 'amber'}>DB {monitoringHealth.database || 'unknown'}</Badge>
              <Badge tone={monitoringHealth.readiness === 'ready' ? 'green' : 'orange'}>{monitoringHealth.readiness || 'unknown'}</Badge>
              <Badge tone={monitoringHealth.environment ? 'slate' : 'neutral'}>{monitoringHealth.environment || 'production'}</Badge>
            </div>
          </div>

          {monitoringIssue && <div className="admin-alert">{monitoringIssue}</div>}

          <section className="admin-metrics-grid monitoring-metrics-grid">
            <MetricCard label="Uptime" value={formatUptime(monitoringSummary.uptime_seconds)} hint={monitoringSummary.uptime_human || 'Live session uptime'} tone="slate" />
            <MetricCard label="Requests / min" value={monitoringSummary.requests_last_minute ?? 0} hint="Rolling request volume" tone="blue" />
            <MetricCard label="Avg latency" value={formatLatency(monitoringSummary.avg_latency_ms)} hint="Average response time" tone="green" />
            <MetricCard label="P95 latency" value={formatLatency(monitoringSummary.p95_latency_ms)} hint="High-percentile response time" tone="orange" />
            <MetricCard label="5xx errors" value={monitoringSummary.error_count ?? 0} hint={`${monitoringSummary.error_rate_percent ?? 0}% of captured traffic`} tone="amber" />
            <MetricCard label="Websocket connections" value={monitoringSummary.websocket_connections ?? 0} hint="Currently connected clients" tone="violet" />
          </section>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Request throughput" subtitle="Average traffic per minute">
              <div className="admin-chart-frame">
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={monitoringLatencySeries}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                    <XAxis dataKey="bucket" stroke="#94a3b8" tickLine={false} axisLine={false} />
                    <YAxis stroke="#94a3b8" tickLine={false} axisLine={false} allowDecimals={false} />
                    <Tooltip labelStyle={{ color: '#0f172a' }} />
                    <Bar dataKey="request_count" radius={[12, 12, 0, 0]} fill="#38bdf8" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>

            <ChartCard title="Latency trend" subtitle="Average response time per minute">
              <div className="admin-chart-frame">
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={monitoringLatencySeries}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                    <XAxis dataKey="bucket" stroke="#94a3b8" tickLine={false} axisLine={false} />
                    <YAxis stroke="#94a3b8" tickLine={false} axisLine={false} tickFormatter={(value) => `${value}ms`} />
                    <Tooltip formatter={(value) => formatLatency(value)} labelStyle={{ color: '#0f172a' }} />
                    <Line type="monotone" dataKey="avg_latency_ms" stroke="#f97316" strokeWidth={3} dot={{ r: 4 }} activeDot={{ r: 6 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>
          </div>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Status class mix" subtitle="2xx, 4xx, and 5xx response split">
              <div className="admin-chart-frame admin-pie-frame">
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie data={monitoringStatusSeries} dataKey="count" nameKey="status_class" cx="50%" cy="50%" outerRadius={102} innerRadius={60} paddingAngle={4}>
                      {monitoringStatusSeries.map((entry, index) => (
                        <Cell key={`status-${entry.status_class}`} fill={paymentColors[index % paymentColors.length]} />
                      ))}
                    </Pie>
                    <Tooltip labelStyle={{ color: '#0f172a' }} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>

            <ChartCard title="Route activity" subtitle="Top monitored endpoints">
              <div className="admin-chart-frame">
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={monitoringRouteSeries} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                    <XAxis type="number" stroke="#94a3b8" tickLine={false} axisLine={false} allowDecimals={false} />
                    <YAxis type="category" dataKey="route" width={180} stroke="#94a3b8" tickLine={false} axisLine={false} />
                    <Tooltip labelStyle={{ color: '#0f172a' }} />
                    <Bar dataKey="count" radius={[0, 12, 12, 0]} fill="#22c55e" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>
          </div>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Recent request log" subtitle="Latest captured API calls" className="span-two">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>Method</th>
                      <th>Route</th>
                      <th>Status</th>
                      <th>Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monitoringRecentRequests.length === 0 ? (
                      <tr>
                        <td colSpan="5">
                          <EmptyState
                            title="No traffic captured yet"
                            description="Monitoring data appears here once the backend records live API requests."
                            actionLabel="Refresh data"
                            onAction={loadDashboard}
                            accent="slate"
                          />
                        </td>
                      </tr>
                    ) : (
                      monitoringRecentRequests.map((requestRow) => (
                        <tr key={`${requestRow.timestamp}-${requestRow.route}-${requestRow.status_code}-${requestRow.method}`}>
                          <td>{requestRow.timestamp}</td>
                          <td>{requestRow.method}</td>
                          <td>{requestRow.route}</td>
                          <td><Badge tone={String(requestRow.status_class || '').startsWith('5') ? 'amber' : 'green'}>{requestRow.status_code}</Badge></td>
                          <td>{formatLatency(requestRow.duration_ms)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>
          </div>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Recent errors" subtitle="5xx events and structured log tail">
              <div className="admin-monitoring-log">
                {monitoringRecentErrors.length === 0 && monitoringLogErrors.length === 0 ? (
                  <EmptyState
                    title="No recent errors"
                    description="The monitoring layer has not captured any server-side failures in the active window."
                    actionLabel="Refresh data"
                    onAction={loadDashboard}
                    accent="green"
                  />
                ) : (
                  <>
                    {monitoringRecentErrors.slice(0, 5).map((item) => (
                      <div className="admin-monitoring-log-line" key={`${item.timestamp}-${item.route}-${item.status_code}`}>
                        <strong>{item.timestamp}</strong>
                        <span>{item.method} {item.route}</span>
                        <span>Status {item.status_code} · {formatLatency(item.duration_ms)}</span>
                        <p>{item.message}</p>
                      </div>
                    ))}
                    {monitoringLogErrors.slice(0, 5).map((line) => (
                      <div className="admin-monitoring-log-line subtle" key={line}>
                        <p>{line}</p>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </ChartCard>

            <ChartCard title="System health" subtitle="Uptime and service status">
              <div className="admin-list-meta compact monitoring-health-grid">
                <span>Database: {monitoringHealth.database || 'unknown'}</span>
                <span>Readiness: {monitoringHealth.readiness || 'unknown'}</span>
                <span>Environment: {monitoringHealth.environment || 'production'}</span>
                <span>Uptime: {monitoringSummary.uptime_human || formatUptime(monitoringSummary.uptime_seconds)}</span>
                <span>Requests: {monitoringSummary.total_requests ?? 0}</span>
                <span>Errors: {monitoringSummary.error_count ?? 0}</span>
              </div>
            </ChartCard>
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current['ai-observability'] = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">AI observability</p>
              <h3>Workflow replay and tool metrics</h3>
            </div>
            <span>{aiToolMetrics.length} tool metric rows</span>
          </div>

          <section className="admin-metrics-grid monitoring-metrics-grid">
            <MetricCard label="Tool runs" value={aiToolMetrics.length} hint="Captured AI executions" tone="blue" />
            <MetricCard label="Workflows" value={aiWorkflows.length} hint="Replay-ready workflow memories" tone="green" />
            <MetricCard label="Action logs" value={aiActionLogs.length} hint="Detailed AI action records" tone="orange" />
            <MetricCard label="Failures" value={aiToolMetrics.filter((metric) => String(metric.execution_status || '').toLowerCase() !== 'success').length} hint="Tool runs needing attention" tone="amber" />
            <MetricCard label="Avg duration" value={formatLatency(aiMetrics.summary?.avg_duration_ms ?? aiToolMetrics.reduce((sum, metric) => sum + Number(metric.duration_ms || 0), 0) / Math.max(aiToolMetrics.length || 1, 1))} hint="Mean AI tool execution time" tone="slate" />
            <MetricCard label="Replayable" value={aiWorkflows.filter((workflow) => workflow.id).length} hint="Stored workflow contexts" tone="violet" />
          </section>

          <div className="admin-grid-two monitoring-charts-grid">
            <ChartCard title="Top AI tools" subtitle="Execution volume and timing">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th>Action</th>
                      <th>Status</th>
                      <th>Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {aiToolMetrics.length === 0 ? (
                      <tr>
                        <td colSpan="4">
                          <EmptyState title="No AI metrics yet" description="AI tool runs will appear here after the backend records execution metrics." accent="blue" />
                        </td>
                      </tr>
                    ) : (
                      aiToolMetrics.slice(0, 10).map((metric, index) => (
                        <tr key={`${metric.id || metric.tool_name || 'metric'}-${index}`}>
                          <td>{metric.tool_name || 'unknown'}</td>
                          <td>{metric.action || 'n/a'}</td>
                          <td><Badge tone={String(metric.execution_status || '').toLowerCase() === 'success' ? 'green' : 'amber'}>{metric.execution_status || 'unknown'}</Badge></td>
                          <td>{formatLatency(metric.duration_ms)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>

            <ChartCard title="Replay workflows" subtitle="Latest memory snapshots">
              <div className="admin-table-wrap">
                <table className="admin-table compact-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>User</th>
                      <th>Workflow</th>
                      <th>Step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {aiWorkflows.length === 0 ? (
                      <tr>
                        <td colSpan="4">
                          <EmptyState title="No workflow memories" description="Replay snapshots will appear once the AI agent stores workflow state." accent="green" />
                        </td>
                      </tr>
                    ) : (
                      aiWorkflows.slice(0, 10).map((workflow) => (
                        <tr key={workflow.id}>
                          <td>{workflow.id}</td>
                          <td>{workflow.user_id || 'unknown'}</td>
                          <td>{workflow.workflow_type || 'unknown'}</td>
                          <td>{workflow.current_step || 'n/a'}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </ChartCard>
          </div>
        </section>

        <section className="admin-metrics-grid">
          <MetricCard label="Total bookings" value={dashboard.summary?.total_bookings ?? visibleBookings.length ?? 0} hint="All records in the system" tone="orange" />
          <MetricCard label="Active shipments" value={dashboard.summary?.active_shipments ?? visibleShipments.filter((shipment) => ['assigned', 'in_transit'].includes(String(shipment.shipment_status || '').toLowerCase())).length} hint="In progress or assigned" tone="blue" />
          <MetricCard label="Revenue collected" value={formatCurrency(dashboard.summary?.revenue_collected ?? visiblePayments.filter((payment) => payment.payment_status === 'paid').reduce((sum, payment) => sum + Number(payment.amount || 0), 0))} hint="Paid advance orders" tone="green" />
          <MetricCard label="Pending payments" value={dashboard.summary?.pending_payments ?? visiblePayments.filter((payment) => payment.payment_status !== 'paid').length} hint="Bookings still awaiting payment" tone="amber" />
          <MetricCard label="Available trucks" value={dashboard.summary?.available_trucks ?? dashboard.trucks?.length ?? 0} hint="Fleet ready for dispatch" tone="slate" />
          <MetricCard label="Drivers on duty" value={dashboard.summary?.drivers_on_duty ?? visibleDrivers.filter((driver) => ['available', 'on_trip'].includes(String(driver.status || '').toLowerCase())).length} hint="Available or in transit" tone="violet" />
        </section>

        <section className="admin-metrics-grid shipment-metrics-grid">
          <MetricCard label="Shipment pending" value={shipmentStatusCounts.pending} hint="Waiting for confirmation" tone="orange" />
          <MetricCard label="Shipment confirmed" value={shipmentStatusCounts.confirmed} hint="Payment cleared" tone="blue" />
          <MetricCard label="Shipment assigned" value={shipmentStatusCounts.assigned} hint="Driver allocated" tone="green" />
          <MetricCard label="In transit" value={shipmentStatusCounts.in_transit} hint="Moving on route" tone="violet" />
          <MetricCard label="Delivered" value={shipmentStatusCounts.delivered} hint="Completed shipments" tone="slate" />
        </section>

        <section className="admin-grid-two">
          <ChartCard title="Revenue analytics" subtitle="Daily paid revenue" className="span-two">
            <div className="admin-chart-frame" ref={(node) => { sectionRefs.current.payments = node }}>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={revenueSeries}>
                  <defs>
                    <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#f97316" stopOpacity={0.9} />
                      <stop offset="95%" stopColor="#f97316" stopOpacity={0.05} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                  <XAxis dataKey="day" stroke="#94a3b8" tickLine={false} axisLine={false} />
                  <YAxis stroke="#94a3b8" tickFormatter={(value) => `₹${value / 1000}k`} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(value) => formatCurrency(value)} labelStyle={{ color: '#0f172a' }} />
                  <Area type="monotone" dataKey="revenue" stroke="#f97316" fill="url(#revenueFill)" strokeWidth={3} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Booking status mix" subtitle="All booking records">
            <div className="admin-chart-frame">
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={bookingStatusSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                  <XAxis dataKey="status" stroke="#94a3b8" tickLine={false} axisLine={false} />
                  <YAxis stroke="#94a3b8" tickLine={false} axisLine={false} allowDecimals={false} />
                  <Tooltip labelStyle={{ color: '#0f172a' }} />
                  <Bar dataKey="count" radius={[12, 12, 0, 0]}>
                    {bookingStatusSeries.map((entry, index) => (
                      <Cell key={`booking-${entry.status}`} fill={revenueColors[index % revenueColors.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </section>

        <section className="admin-grid-two" ref={(node) => { sectionRefs.current.bookings = node }}>
          <ChartCard title="Payment status distribution" subtitle="Razorpay and booking payments">
            <div className="admin-chart-frame admin-pie-frame">
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie data={paymentStatusSeries} dataKey="count" nameKey="status" cx="50%" cy="50%" outerRadius={108} innerRadius={62} paddingAngle={4}>
                    {paymentStatusSeries.map((entry, index) => (
                      <Cell key={`payment-${entry.status}`} fill={paymentColors[index % paymentColors.length]} />
                    ))}
                  </Pie>
                  <Tooltip labelStyle={{ color: '#0f172a' }} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Revenue trend" subtitle={`Filtered over ${days} days`}>
            <div className="admin-chart-frame">
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={revenueSeries}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148, 163, 184, 0.18)" />
                  <XAxis dataKey="day" stroke="#94a3b8" tickLine={false} axisLine={false} />
                  <YAxis stroke="#94a3b8" tickLine={false} axisLine={false} tickFormatter={(value) => `₹${value / 1000}k`} />
                  <Tooltip formatter={(value) => formatCurrency(value)} labelStyle={{ color: '#0f172a' }} />
                  <Line type="monotone" dataKey="revenue" stroke="#38bdf8" strokeWidth={3} dot={{ r: 4 }} activeDot={{ r: 6 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.shipments = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Shipments</p>
              <h3>Active shipment tracker</h3>
            </div>
            <span>{dashboard.shipments?.length || 0} shipments</span>
          </div>

          <div className="admin-cards-grid three-up">
            {visibleShipments.length === 0 ? (
              <EmptyState
                title="No shipments to show"
                description="Live shipments will appear here after they are created in the backend and synced to the admin dashboard."
                accent="blue"
              />
            ) : (
              visibleShipments.map((shipment) => {
                const draft = shipmentDrafts[shipment.id] || {}

                return (
                  <article className="admin-list-card" key={shipment.id}>
                    <div className="admin-list-card-top">
                      <div>
                        <h4>{shipment.booking_reference || `Shipment #${shipment.id}`}</h4>
                        <p>{shipment.pickup_location || 'Pickup pending'} → {shipment.drop_location || 'Drop pending'}</p>
                      </div>
                      <Badge tone={shipment.payment_status === 'paid' ? 'green' : 'amber'}>{shipment.payment_status || 'pending'}</Badge>
                    </div>
                    <div className="admin-list-meta">
                      <span>Cargo: {shipment.cargo_type || 'General'}</span>
                      <span>Truck: {shipment.truck_type || 'Pending'}</span>
                      <span>Driver ID: {shipment.assigned_driver_id ?? 'Unassigned'}</span>
                      <span>Status: {shipment.shipment_status || 'pending'}</span>
                    </div>
                    <div className="admin-edit-grid">
                      <label>
                        Shipment status
                        <select value={draft.shipment_status || shipment.shipment_status} onChange={(event) => updateShipmentDraft(shipment.id, 'shipment_status', event.target.value)}>
                          {shipmentStatusOptions.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Assign driver
                        <select value={draft.assigned_driver_id ?? shipment.assigned_driver_id ?? ''} onChange={(event) => updateShipmentDraft(shipment.id, 'assigned_driver_id', event.target.value)}>
                          <option value="">Unassigned</option>
                          {(dashboard.drivers || []).map((driver) => (
                            <option key={driver.id} value={driver.id}>
                              {driver.driver_name} ({driver.status || 'available'})
                            </option>
                          ))}
                        </select>
                      </label>
                      <button type="button" className="admin-save-button" onClick={() => saveShipment(shipment)} disabled={savingShipmentId === shipment.id}>
                        {savingShipmentId === shipment.id ? 'Saving...' : 'Save shipment'}
                      </button>
                    </div>
                  </article>
                )
              })
            )}
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.drivers = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Drivers</p>
              <h3>Driver management</h3>
            </div>
            <span>{dashboard.drivers?.length || 0} drivers</span>
          </div>

          <div className="admin-cards-grid three-up">
            {visibleDrivers.length === 0 ? (
              <EmptyState
                title="No drivers loaded"
                description="Driver records will appear here once the backend has live roster data to display."
                accent="green"
              />
            ) : (
              visibleDrivers.map((driver) => {
                const draft = driverDrafts[driver.id] || {}

                return (
                  <article className="admin-list-card" key={driver.id}>
                    <div className="admin-list-card-top">
                      <div>
                        <h4>{driver.driver_name}</h4>
                        <p>{driver.license_number}</p>
                      </div>
                      <Badge tone={driver.status === 'available' ? 'green' : driver.status === 'on_trip' ? 'orange' : 'slate'}>{driver.status}</Badge>
                    </div>

                    <div className="admin-list-meta compact">
                      <span>{driver.phone || 'No phone'}</span>
                      <span>{driver.assigned_truck || 'Unassigned'}</span>
                      <span>{driver.assigned_truck_type || 'Truck type pending'}</span>
                      <span>{driver.experience_years} years exp</span>
                    </div>

                    <div className="admin-edit-grid">
                      <label>
                        Status
                        <select value={draft.status || driver.status} onChange={(event) => updateDriverDraft(driver.id, 'status', event.target.value)}>
                          {driverStatusOptions.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Assigned truck
                        <input
                          type="text"
                          value={draft.assigned_truck ?? driver.assigned_truck ?? ''}
                          onChange={(event) => updateDriverDraft(driver.id, 'assigned_truck', event.target.value)}
                          placeholder="Truck number"
                        />
                      </label>
                      <button type="button" className="admin-save-button" onClick={() => saveDriver(driver)} disabled={savingDriverId === driver.id}>
                        {savingDriverId === driver.id ? 'Saving...' : 'Save driver'}
                      </button>
                    </div>
                  </article>
                )
              })
            )}
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.trucks = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Trucks</p>
              <h3>Truck management</h3>
            </div>
            <span>{dashboard.trucks?.length || 0} trucks tracked</span>
          </div>

          <div className="admin-cards-grid three-up">
            {(dashboard.trucks || []).length === 0 ? (
              <EmptyState
                title="Truck fleet is empty"
                description="Truck telemetry will appear here when the live fleet tables contain active records."
                accent="amber"
              />
            ) : (
              (dashboard.trucks || []).map((truck) => {
                const truckKey = `${truck.truck_type}-${truck.vehicle_number}`
                const draft = truckDrafts[truckKey] || {}

                return (
                  <article className="admin-list-card" key={truckKey}>
                    <div className="admin-list-card-top">
                      <div>
                        <h4>{truck.vehicle_number}</h4>
                        <p>{truck.truck_type}</p>
                      </div>
                      <Badge tone={truck.availability_status === 'available' ? 'green' : 'slate'}>{truck.availability_status || 'unknown'}</Badge>
                    </div>

                    <div className="admin-list-meta compact">
                      <span>{truck.latitude ?? 'No GPS'}</span>
                      <span>{truck.longitude ?? 'No GPS'}</span>
                      <span>{truck.source_table || 'Fleet table'}</span>
                      <span>{formatDate(truck.last_updated)}</span>
                    </div>

                    <div className="admin-edit-grid">
                      <label>
                        Availability
                        <select value={draft.availability_status || truck.availability_status} onChange={(event) => updateTruckDraft(truckKey, event.target.value)}>
                          {truckStatusOptions.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button type="button" className="admin-save-button" onClick={() => saveTruck(truck)} disabled={savingTruckKey === truckKey}>
                        {savingTruckKey === truckKey ? 'Saving...' : 'Save truck'}
                      </button>
                    </div>
                  </article>
                )
              })
            )}
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.vehicles = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Vehicles</p>
              <h3>Fleet records</h3>
            </div>
            <span>{visibleVehicles.length} vehicle records</span>
          </div>

          <div className="admin-sidebar-card" style={{ marginBottom: '20px' }}>
            <p>New vehicle</p>
            <div className="admin-edit-grid">
              <label>
                Code
                <input type="text" value={newVehicleDraft.vehicle_code} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, vehicle_code: event.target.value }))} placeholder="vehicle-001" />
              </label>
              <label>
                Name
                <input type="text" value={newVehicleDraft.vehicle_name} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, vehicle_name: event.target.value }))} placeholder="Fleet name" />
              </label>
              <label>
                Truck type
                <select value={newVehicleDraft.truck_type} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, truck_type: event.target.value }))}>
                  {['12 tyre', '14 tyre', '16 tyre'].map((type) => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                </select>
              </label>
              <label>
                Max load (t)
                <input type="number" min="0" step="0.1" value={newVehicleDraft.max_load_tons} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, max_load_tons: event.target.value }))} placeholder="25" />
              </label>
              <label>
                Rate / km
                <input type="number" min="0" step="0.1" value={newVehicleDraft.rate_per_km} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, rate_per_km: event.target.value }))} placeholder="45" />
              </label>
              <label>
                Status
                <select value={newVehicleDraft.availability_status} onChange={(event) => setNewVehicleDraft((previous) => ({ ...previous, availability_status: event.target.value }))}>
                  {truckStatusOptions.map((status) => (
                    <option key={status} value={status}>{status}</option>
                  ))}
                </select>
              </label>
              <button type="button" className="admin-save-button" onClick={createVehicle} disabled={creatingVehicle}>
                {creatingVehicle ? 'Creating...' : 'Create vehicle'}
              </button>
            </div>
          </div>

          <div className="admin-cards-grid three-up">
            {visibleVehicles.length === 0 ? (
              <EmptyState
                title="No vehicles loaded"
                description="Vehicle records from the new fleet table will show up here once available."
                accent="slate"
              />
            ) : (
              visibleVehicles.map((vehicle) => {
                const draft = vehicleDrafts[vehicle.vehicle_code] || {}

                return (
                  <article className="admin-list-card" key={vehicle.vehicle_code}>
                    <div className="admin-list-card-top">
                      <div>
                        <h4>{vehicle.vehicle_name || vehicle.vehicle_code}</h4>
                        <p>{vehicle.vehicle_code}</p>
                      </div>
                      <Badge tone={vehicle.availability_status === 'available' ? 'green' : 'slate'}>{vehicle.availability_status || 'unknown'}</Badge>
                    </div>

                    <div className="admin-list-meta compact">
                      <span>Type: {vehicle.truck_type || 'n/a'}</span>
                      <span>Load: {vehicle.max_load_tons ?? 'n/a'} t</span>
                      <span>Rate: {vehicle.rate_per_km ?? 'n/a'}/km</span>
                      <span>Booking: {vehicle.current_booking_id ?? 'none'}</span>
                    </div>

                    <div className="admin-edit-grid">
                      <label>
                        Name
                        <input type="text" value={draft.vehicle_name ?? vehicle.vehicle_name ?? ''} onChange={(event) => updateVehicleDraft(vehicle.vehicle_code, 'vehicle_name', event.target.value)} />
                      </label>
                      <label>
                        Availability
                        <select value={draft.availability_status || vehicle.availability_status || 'available'} onChange={(event) => updateVehicleDraft(vehicle.vehicle_code, 'availability_status', event.target.value)}>
                          {truckStatusOptions.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button type="button" className="admin-save-button" onClick={() => saveVehicle(vehicle)} disabled={savingVehicleCode === vehicle.vehicle_code}>
                        {savingVehicleCode === vehicle.vehicle_code ? 'Saving...' : 'Save vehicle'}
                      </button>
                      <button
                        type="button"
                        className="admin-save-button secondary"
                        onClick={async () => {
                          if (!window.confirm(`Delete vehicle ${vehicle.vehicle_code}?`)) return
                          setSavingVehicleCode(vehicle.vehicle_code)
                          setError('')
                          try {
                            await deleteAdminVehicle(vehicle.vehicle_code)
                            await loadDashboard()
                          } catch (deleteError) {
                            setError(deleteError instanceof Error ? deleteError.message : 'Unable to delete vehicle')
                          } finally {
                            setSavingVehicleCode(null)
                          }
                        }}
                        disabled={savingVehicleCode === vehicle.vehicle_code}
                      >
                        Delete
                      </button>
                    </div>
                  </article>
                )
              })
            )}
          </div>
        </section>

        <section className="admin-panel" ref={(node) => { sectionRefs.current.payments = node }}>
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Bookings</p>
              <h3>Booking ledger</h3>
            </div>
            <span>{dashboard.bookings?.length || 0} filtered bookings</span>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Booking</th>
                  <th>Customer</th>
                  <th>Route</th>
                  <th>Status</th>
                  <th>Payment</th>
                  <th>Truck</th>
                  <th>Amount</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {visibleBookings.length === 0 ? (
                  <tr>
                    <td colSpan="8">
                      <EmptyState
                        title="No booking rows yet"
                        description="Booking rows will populate automatically from persisted shipment records."
                        accent="blue"
                      />
                    </td>
                  </tr>
                ) : (
                  visibleBookings.map((booking) => (
                    <tr key={booking.id}>
                      <td>#{booking.id}</td>
                      <td>
                        <strong>{booking.customer_name || 'Customer'}</strong>
                        <span>{booking.phone || 'No phone'}</span>
                      </td>
                      <td>
                        <strong>{booking.pickup_location || 'Pickup pending'}</strong>
                        <span>{booking.drop_location || 'Drop pending'}</span>
                      </td>
                      <td>
                        <Badge tone={booking.booking_status === 'completed' ? 'green' : booking.booking_status === 'cancelled' ? 'slate' : 'orange'}>{booking.booking_status}</Badge>
                      </td>
                      <td>
                        <Badge tone={booking.payment_status === 'paid' ? 'green' : 'amber'}>{booking.payment_status}</Badge>
                      </td>
                      <td>{booking.lorry_number || 'Unassigned'}</td>
                      <td>{formatCurrency(booking.price || booking.payment_amount || 0)}</td>
                      <td>{formatDate(booking.created_at)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="admin-grid-two" ref={(node) => { sectionRefs.current.audit = node }}>
          <ChartCard title="Audit log stream" subtitle="Tracked mutations and admin actions">
            <div className="admin-table-actions">
              <button type="button" className="admin-save-button secondary" onClick={purgeAuditLogs} disabled={purgingAuditLogs}>
                {purgingAuditLogs ? 'Purging...' : 'Purge 30-day logs'}
              </button>
            </div>
            <div className="admin-table-wrap">
              <table className="admin-table compact-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Action</th>
                    <th>Entity</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleAuditLogs.length === 0 ? (
                    <tr>
                      <td colSpan="6">
                        <EmptyState title="No audit logs yet" description="Admin writes and AI audit events will appear here once the backend captures them." accent="amber" />
                      </td>
                    </tr>
                  ) : (
                    visibleAuditLogs.slice(0, 12).map((entry) => (
                      <tr key={entry.id}>
                        <td>{formatDate(entry.created_at)}</td>
                        <td>{entry.user_id || 'system'}</td>
                        <td>{entry.action || 'n/a'}</td>
                        <td>{entry.entity_type || 'n/a'} {entry.entity_id ? `#${entry.entity_id}` : ''}</td>
                        <td><Badge tone={entry.status === 'success' ? 'green' : 'amber'}>{entry.status || 'unknown'}</Badge></td>
                        <td>-</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </ChartCard>

          <ChartCard title="Webhook events" subtitle="Inbound integration traffic">
            <div className="admin-table-wrap">
              <table className="admin-table compact-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Source</th>
                    <th>Event</th>
                    <th>Status</th>
                    <th>Code</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleWebhookEvents.length === 0 ? (
                    <tr>
                      <td colSpan="6">
                        <EmptyState title="No webhook events yet" description="Webhook payloads from partner systems will appear here when ingested." accent="green" />
                      </td>
                    </tr>
                  ) : (
                    visibleWebhookEvents.slice(0, 12).map((entry) => (
                      <tr key={entry.id}>
                        <td>{formatDate(entry.created_at)}</td>
                        <td>{entry.source || 'unknown'}</td>
                        <td>{entry.event_type || 'unknown'}</td>
                        <td><Badge tone={entry.status === 'received' ? 'green' : 'amber'}>{entry.status || 'unknown'}</Badge></td>
                        <td>{entry.response_code ?? 'n/a'}</td>
                        <td>
                          <button
                            type="button"
                            className="admin-save-button secondary"
                            onClick={() => retryWebhookEvent(entry.id)}
                            disabled={retryingWebhookId === entry.id}
                          >
                            {retryingWebhookId === entry.id ? 'Retrying...' : 'Retry'}
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </ChartCard>
        </section>

        <section className="admin-panel">
          <div className="admin-card-heading">
            <div>
              <p className="admin-kicker">Payments</p>
              <h3>Recent payment records</h3>
            </div>
            <span>{dashboard.payment_records?.length || 0} recent records</span>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table compact-table">
              <thead>
                <tr>
                  <th>Payment</th>
                  <th>Booking</th>
                  <th>Status</th>
                  <th>Order ID</th>
                  <th>Payment ID</th>
                  <th>Amount</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {visiblePayments.length === 0 ? (
                  <tr>
                    <td colSpan="7">
                      <EmptyState
                        title="No recent payments"
                        description="Payment history will appear once transactions are captured in the backend ledger."
                        accent="green"
                      />
                    </td>
                  </tr>
                ) : (
                  visiblePayments.map((payment) => (
                    <tr key={payment.id}>
                      <td>#{payment.id}</td>
                      <td>#{payment.booking_id}</td>
                      <td><Badge tone={payment.payment_status === 'paid' ? 'green' : 'amber'}>{payment.payment_status}</Badge></td>
                      <td>{payment.razorpay_order_id || 'Pending'}</td>
                      <td>{payment.razorpay_payment_id || 'Pending'}</td>
                      <td>{formatCurrency(payment.amount)}</td>
                      <td>{formatDate(payment.created_at)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  )
}
