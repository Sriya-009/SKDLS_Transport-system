import { requestJson } from './http'

export async function fetchAdminDashboard({ bookingStatus = 'all', paymentStatus = 'all', days = 30 } = {}) {
  const params = new URLSearchParams()

  if (bookingStatus && bookingStatus !== 'all') {
    params.set('booking_status', bookingStatus)
  }

  if (paymentStatus && paymentStatus !== 'all') {
    params.set('payment_status', paymentStatus)
  }

  if (days) {
    params.set('days', String(days))
  }

  return requestJson(`/api/admin/dashboard?${params.toString()}`)
}

export async function fetchAdminMonitoring({ days = 30 } = {}) {
  const params = new URLSearchParams()

  if (days) {
    params.set('days', String(days))
  }

  return requestJson(`/api/admin/monitoring?${params.toString()}`)
}

export async function fetchAdminControlTower({ days = 30 } = {}) {
  const params = new URLSearchParams()
  if (days) params.set('days', String(days))
  return requestJson(`/api/admin/control-tower?${params.toString()}`)
}

export async function fetchAdminPaymentAnalytics({ days = 30 } = {}) {
  const params = new URLSearchParams()
  if (days) params.set('days', String(days))
  return requestJson(`/api/admin/payments/analytics?${params.toString()}`)
}

export async function fetchAdminPaymentAnomalies({ days = 30 } = {}) {
  const params = new URLSearchParams()
  if (days) params.set('days', String(days))
  return requestJson(`/api/admin/payments/anomalies?${params.toString()}`)
}

export async function fetchAdminDrivers() {
  return requestJson('/api/admin/drivers')
}

export async function fetchAdminUsers() {
  return requestJson('/api/admin/users')
}

export async function createAdminDriver(payload) {
  return requestJson('/api/admin/drivers', {
    method: 'POST',
    body: payload,
  })
}

export async function updateAdminDriver(driverId, payload) {
  return requestJson(`/api/admin/drivers/${encodeURIComponent(driverId)}`, {
    method: 'PUT',
    body: payload,
  })
}

export async function deleteAdminDriver(driverId) {
  return requestJson(`/api/admin/drivers/${encodeURIComponent(driverId)}`, {
    method: 'DELETE',
  })
}

export async function generateAdminDriverApiKey(driverId) {
  return requestJson(`/api/admin/drivers/${encodeURIComponent(driverId)}/generate-api-key`, {
    method: 'POST',
  })
}

export async function resetAdminDriverPassword(driverId, payload = {}) {
  return requestJson(`/api/admin/drivers/${encodeURIComponent(driverId)}/reset-password`, {
    method: 'POST',
    body: payload,
  })
}

export async function updateAdminTruck(truckTypeCode, vehicleNumber, payload) {
  return requestJson(
    `/admin/trucks/${encodeURIComponent(truckTypeCode)}/${encodeURIComponent(vehicleNumber)}`,
    {
      method: 'PATCH',
      body: payload,
    },
  )
}

export async function fetchAdminShipments() {
  return requestJson('/api/admin/shipments')
}

export async function fetchAdminVehicles() {
  return requestJson('/api/admin/vehicles')
}

export async function createAdminVehicle(payload) {
  return requestJson('/api/admin/vehicles', {
    method: 'POST',
    body: payload,
  })
}

export async function updateAdminVehicle(vehicleCode, payload) {
  return requestJson(`/api/admin/vehicles/${encodeURIComponent(vehicleCode)}`, {
    method: 'PUT',
    body: payload,
  })
}

export async function deleteAdminVehicle(vehicleCode) {
  return requestJson(`/api/admin/vehicles/${encodeURIComponent(vehicleCode)}`, {
    method: 'DELETE',
  })
}

export async function fetchAdminAuditLogs(params = {}) {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.perPage) query.set('per_page', String(params.perPage))
  if (params.userId) query.set('user_id', params.userId)
  if (params.role) query.set('role', params.role)
  if (params.action) query.set('action', params.action)
  if (params.entityType) query.set('entity_type', params.entityType)
  if (params.status) query.set('status', params.status)
  if (params.correlationId) query.set('correlation_id', params.correlationId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/audit-logs${suffix}`)
}

export async function deleteAdminAuditLogs(params = {}) {
  const query = new URLSearchParams()
  if (params.olderThanDays) query.set('older_than_days', String(params.olderThanDays))
  else if (params.days) query.set('older_than_days', String(params.days))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/audit-logs${suffix}`, {
    method: 'DELETE',
  })
}

export async function fetchAdminWebhookEvents(params = {}) {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.perPage) query.set('per_page', String(params.perPage))
  if (params.source) query.set('source', params.source)
  if (params.eventType) query.set('event_type', params.eventType)
  if (params.status) query.set('status', params.status)
  if (params.correlationId) query.set('correlation_id', params.correlationId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/webhooks/events${suffix}`)
}

export async function retryAdminWebhookEvent(eventId, payload = {}) {
  return requestJson(`/api/admin/webhooks/events/${encodeURIComponent(eventId)}/retry`, {
    method: 'PATCH',
    body: payload,
  })
}

export async function fetchAdminAiMetrics(params = {}) {
  const query = new URLSearchParams()
  if (params.userId) query.set('user_id', params.userId)
  if (params.action) query.set('action', params.action)
  if (params.status) query.set('status', params.status)
  if (params.shipmentId) query.set('shipment_id', String(params.shipmentId))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/ai/metrics${suffix}`)
}

export async function fetchAdminAiActionLogs(params = {}) {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.perPage) query.set('per_page', String(params.perPage))
  if (params.userId) query.set('user_id', params.userId)
  if (params.action) query.set('action', params.action)
  if (params.status) query.set('status', params.status)
  if (params.shipmentId) query.set('shipment_id', String(params.shipmentId))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/ai/action-logs${suffix}`)
}

export async function fetchAdminAiWorkflows(params = {}) {
  const query = new URLSearchParams()
  if (params.userId) query.set('user_id', params.userId)
  if (params.workflowType) query.set('workflow_type', params.workflowType)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/ai/workflows${suffix}`)
}

export async function replayAdminAiWorkflow(workflowId) {
  return requestJson(`/api/admin/ai/replay/${encodeURIComponent(workflowId)}`, {
    method: 'POST',
  })
}

export async function fetchAdminAiWorkflowRetries(params = {}) {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  if (params.limit) query.set('limit', String(params.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return requestJson(`/api/admin/ai/workflow-retries${suffix}`)
}

export async function updateAdminAiWorkflowRetry(jobId, payload = {}) {
  return requestJson(`/api/admin/ai/workflow-retries/${encodeURIComponent(jobId)}`, {
    method: 'PATCH',
    body: payload,
  })
}

export async function updateAdminShipmentStatus(shipmentId, payload) {
  return requestJson(`/api/admin/shipments/${encodeURIComponent(shipmentId)}/status`, {
    method: 'PUT',
    body: payload,
  })
}

export async function requestPaymentRefund(payload = {}) {
  return requestJson('/payments/refund', {
    method: 'POST',
    body: payload,
  })
}
