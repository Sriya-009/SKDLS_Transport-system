import { useDeferredValue, useEffect, useMemo, useState } from 'react'
import {
  createAdminDriver,
  deleteAdminDriver,
  fetchAdminDashboard,
  fetchAdminDrivers,
  fetchAdminUsers,
  generateAdminDriverApiKey,
  resetAdminDriverPassword,
  updateAdminDriver,
} from '../services/adminService'
import '../styles/AdminManagementDashboard.css'

const sidebarItems = [
  { id: 'overview', label: 'Overview' },
  { id: 'drivers', label: 'Drivers' },
  { id: 'users', label: 'Users' },
  { id: 'api-keys', label: 'API Keys' },
]

const statusOptions = ['all', 'available', 'on_trip', 'maintenance', 'on_leave']
const roleOptions = ['all', 'customer', 'admin', 'driver']

const emptyDriverForm = {
  id: null,
  driver_name: '',
  phone: '',
  license_number: '',
  assigned_truck: '',
  assigned_truck_type: '',
  status: 'available',
  rating: '4.8',
  experience_years: '0',
  password: '',
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

function maskSecret(value) {
  const text = String(value || '').trim()
  if (!text) {
    return 'Not generated'
  }

  if (text.length <= 12) {
    return text
  }

  return `${text.slice(0, 6)}…${text.slice(-4)}`
}

function Badge({ children, tone = 'neutral' }) {
  return <span className={`admin-badge ${tone}`}>{children}</span>
}

function ToastStack({ toasts, onClose }) {
  return (
    <div className="admin-toast-stack" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div key={toast.id} className={`admin-toast ${toast.type}`}>
          <strong>{toast.title}</strong>
          <p>{toast.message}</p>
          <button type="button" onClick={() => onClose(toast.id)}>Dismiss</button>
        </div>
      ))}
    </div>
  )
}

function ModalShell({ title, subtitle, children, onClose }) {
  return (
    <div className="admin-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="admin-modal" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <div className="admin-modal-header">
          <div>
            <p className="admin-kicker">Admin action</p>
            <h3>{title}</h3>
            {subtitle && <span>{subtitle}</span>}
          </div>
          <button type="button" className="admin-icon-button" onClick={onClose} aria-label="Close modal">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export default function AdminManagementDashboard() {
  const [dashboard, setDashboard] = useState({ summary: {} })
  const [drivers, setDrivers] = useState([])
  const [users, setUsers] = useState([])
  const [activeSection, setActiveSection] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [savingId, setSavingId] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [roleFilter, setRoleFilter] = useState('all')
  const [toastList, setToastList] = useState([])
  const [formMode, setFormMode] = useState('create')
  const [driverForm, setDriverForm] = useState(emptyDriverForm)
  const [driverModalOpen, setDriverModalOpen] = useState(false)
  const [resetModalDriver, setResetModalDriver] = useState(null)
  const [resetPasswordValue, setResetPasswordValue] = useState('')
  const [deleteModalDriver, setDeleteModalDriver] = useState(null)

  const deferredSearch = useDeferredValue(searchQuery)

  const pushToast = (type, title, message) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setToastList((previous) => [...previous, { id, type, title, message }])
    window.setTimeout(() => {
      setToastList((previous) => previous.filter((item) => item.id !== id))
    }, 3800)
  }

  const closeToast = (toastId) => {
    setToastList((previous) => previous.filter((item) => item.id !== toastId))
  }

  const loadData = async () => {
    setLoading(true)
    try {
      const [dashboardData, driverData, userData] = await Promise.all([
        fetchAdminDashboard(),
        fetchAdminDrivers(),
        fetchAdminUsers(),
      ])

      setDashboard(dashboardData)
      setDrivers(Array.isArray(driverData.drivers) ? driverData.drivers : [])
      setUsers(Array.isArray(userData.users) ? userData.users : [])
    } catch (error) {
      pushToast('error', 'Load failed', error instanceof Error ? error.message : 'Unable to load admin data')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const filteredDrivers = useMemo(() => {
    const query = String(deferredSearch || '').trim().toLowerCase()

    return drivers.filter((driver) => {
      const matchesStatus = statusFilter === 'all' || String(driver.status || '').toLowerCase() === statusFilter
      const haystack = [driver.driver_name, driver.phone, driver.license_number, driver.assigned_truck, driver.assigned_truck_type, driver.api_key]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      const matchesQuery = !query || haystack.includes(query)
      return matchesStatus && matchesQuery
    })
  }, [deferredSearch, drivers, statusFilter])

  const filteredUsers = useMemo(() => {
    const query = String(deferredSearch || '').trim().toLowerCase()

    return users.filter((user) => {
      const matchesRole = roleFilter === 'all' || String(user.role || '').toLowerCase() === roleFilter
      const haystack = [user.full_name, user.email, user.phone, user.api_key].filter(Boolean).join(' ').toLowerCase()
      const matchesQuery = !query || haystack.includes(query)
      return matchesRole && matchesQuery
    })
  }, [deferredSearch, roleFilter, users])

  const openCreateModal = () => {
    setFormMode('create')
    setDriverForm(emptyDriverForm)
    setDriverModalOpen(true)
  }

  const openEditModal = (driver) => {
    setFormMode('edit')
    setDriverForm({
      id: driver.id,
      driver_name: driver.driver_name || '',
      phone: driver.phone || '',
      license_number: driver.license_number || '',
      assigned_truck: driver.assigned_truck || '',
      assigned_truck_type: driver.assigned_truck_type || '',
      status: driver.status || 'available',
      rating: String(driver.rating ?? '4.8'),
      experience_years: String(driver.experience_years ?? '0'),
      password: '',
    })
    setDriverModalOpen(true)
  }

  const copyToClipboard = async (value, label = 'Value') => {
    const text = String(value || '').trim()
    if (!text) {
      pushToast('warning', 'Nothing to copy', `${label} is empty`)
      return
    }

    try {
      await navigator.clipboard.writeText(text)
      pushToast('success', 'Copied', `${label} copied to clipboard`)
    } catch {
      pushToast('error', 'Copy failed', `Unable to copy ${label.toLowerCase()}`)
    }
  }

  const submitDriverForm = async (event) => {
    event.preventDefault()
    setSavingId('driver-form')

    try {
      const payload = {
        driver_name: driverForm.driver_name,
        phone: driverForm.phone,
        license_number: driverForm.license_number,
        assigned_truck: driverForm.assigned_truck,
        assigned_truck_type: driverForm.assigned_truck_type,
        status: driverForm.status,
        rating: Number(driverForm.rating || 0),
        experience_years: Number(driverForm.experience_years || 0),
      }

      if (formMode === 'create') {
        payload.password = driverForm.password
        await createAdminDriver(payload)
        pushToast('success', 'Driver created', 'The driver profile has been added')
      } else {
        await updateAdminDriver(driverForm.id, payload)
        pushToast('success', 'Driver updated', 'Driver profile changes were saved')
      }

      setDriverModalOpen(false)
      await loadData()
    } catch (error) {
      pushToast('error', 'Save failed', error instanceof Error ? error.message : 'Unable to save driver')
    } finally {
      setSavingId(null)
    }
  }

  const handleGenerateApiKey = async (driver) => {
    setSavingId(`api-${driver.id}`)
    try {
      const response = await generateAdminDriverApiKey(driver.id)
      const apiKey = response.api_key || response.driver?.api_key
      if (apiKey) {
        await copyToClipboard(apiKey, 'API key')
      }
      pushToast('success', 'API key generated', `A fresh device key was created for ${driver.driver_name}`)
      await loadData()
    } catch (error) {
      pushToast('error', 'Generation failed', error instanceof Error ? error.message : 'Unable to generate API key')
    } finally {
      setSavingId(null)
    }
  }

  const openResetModal = (driver) => {
    setResetModalDriver(driver)
    setResetPasswordValue('')
  }

  const submitResetPassword = async (event) => {
    event.preventDefault()
    if (!resetModalDriver) {
      return
    }

    setSavingId(`reset-${resetModalDriver.id}`)
    try {
      const response = await resetAdminDriverPassword(resetModalDriver.id, resetPasswordValue ? { new_password: resetPasswordValue } : {})
      if (response.temporary_password) {
        await copyToClipboard(response.temporary_password, 'Temporary password')
      }
      pushToast('success', 'Password reset', `Driver password reset for ${resetModalDriver.driver_name}`)
      setResetModalDriver(null)
      await loadData()
    } catch (error) {
      pushToast('error', 'Reset failed', error instanceof Error ? error.message : 'Unable to reset password')
    } finally {
      setSavingId(null)
    }
  }

  const confirmDeleteDriver = async () => {
    if (!deleteModalDriver) {
      return
    }

    setSavingId(`delete-${deleteModalDriver.id}`)
    try {
      await deleteAdminDriver(deleteModalDriver.id)
      pushToast('success', 'Driver deleted', `${deleteModalDriver.driver_name} was removed`)
      setDeleteModalDriver(null)
      await loadData()
    } catch (error) {
      pushToast('error', 'Delete failed', error instanceof Error ? error.message : 'Unable to delete driver')
    } finally {
      setSavingId(null)
    }
  }

  const activeTruckCount = dashboard.summary?.live_trucks ?? 0
  const activeDriverCount = dashboard.summary?.drivers_on_duty ?? 0
  const totalBookings = dashboard.summary?.total_bookings ?? 0
  const revenueCollected = dashboard.summary?.revenue_collected ?? 0

  return (
    <div className="admin-dashboard-shell">
      <ToastStack toasts={toastList} onClose={closeToast} />

      <aside className="admin-sidebar">
        <div className="admin-brand-card">
          <p className="admin-kicker">Control tower</p>
          <h1>Admin Management</h1>
          <span>Drivers, users, and GPS keys in one secure cockpit.</span>
        </div>

        <nav className="admin-side-nav" aria-label="Admin sections">
          {sidebarItems.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`admin-side-link ${activeSection === item.id ? 'active' : ''}`}
              onClick={() => {
                setActiveSection(item.id)
                const sectionElement = document.getElementById(item.id)
                sectionElement?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="admin-sidebar-panel">
          <label>
            Search
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Driver, user, phone, key"
            />
          </label>

          <label>
            Driver status
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              {statusOptions.map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
          </label>

          <label>
            User role
            <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
              {roleOptions.map((role) => (
                <option key={role} value={role}>{role}</option>
              ))}
            </select>
          </label>

          <button type="button" className="admin-primary-button" onClick={loadData} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh data'}
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <header className="admin-hero" id="overview">
          <div>
            <p className="admin-kicker">Premium logistics operations</p>
            <h2>Secure admin management dashboard</h2>
            <p>
              Manage driver profiles, rotate GPS API keys, and review platform activity from a dark control-tower workspace.
            </p>
          </div>
          <div className="admin-hero-meta">
            <Badge tone="orange">{activeTruckCount} live trucks</Badge>
            <Badge tone="blue">{activeDriverCount} drivers on duty</Badge>
            <Badge tone="green">{totalBookings} bookings</Badge>
            <Badge tone="violet">₹{Number(revenueCollected || 0).toLocaleString('en-IN')} revenue</Badge>
          </div>
        </header>

        {loading ? (
          <section className="admin-loading-card">
            <div className="admin-spinner" />
            <div>
              <h3>Loading management data</h3>
              <p>Pulling live drivers, users, and dashboard metrics.</p>
            </div>
          </section>
        ) : null}

        <section className="admin-grid">
          <article className="admin-stat-card">
            <span>Total bookings</span>
            <strong>{totalBookings}</strong>
            <p>All bookings captured in the system.</p>
          </article>
          <article className="admin-stat-card">
            <span>Active shipments</span>
            <strong>{dashboard.summary?.active_shipments ?? 0}</strong>
            <p>Currently in progress or assigned.</p>
          </article>
          <article className="admin-stat-card">
            <span>Available trucks</span>
            <strong>{dashboard.summary?.available_trucks ?? 0}</strong>
            <p>Fleet ready to dispatch.</p>
          </article>
          <article className="admin-stat-card">
            <span>Pending payments</span>
            <strong>{dashboard.summary?.pending_payments ?? 0}</strong>
            <p>Bookings waiting for payment confirmation.</p>
          </article>
        </section>

        <section className="admin-panel" id="drivers">
          <div className="admin-panel-header">
            <div>
              <p className="admin-kicker">Drivers</p>
              <h3>Driver management</h3>
            </div>
            <button type="button" className="admin-primary-button" onClick={openCreateModal}>
              Create driver
            </button>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Phone</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>API key</th>
                  <th>Last active</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredDrivers.map((driver) => (
                  <tr key={driver.id}>
                    <td>
                      <strong>{driver.driver_name}</strong>
                      <span>{driver.license_number || 'No license'}</span>
                    </td>
                    <td>{driver.phone || 'No phone'}</td>
                    <td><Badge tone="neutral">{driver.role || 'driver'}</Badge></td>
                    <td><Badge tone={driver.status === 'available' ? 'green' : driver.status === 'on_trip' ? 'orange' : 'slate'}>{driver.status || 'available'}</Badge></td>
                    <td>
                      <div className="admin-secret-cell">
                        <code>{maskSecret(driver.api_key)}</code>
                        <div className="admin-inline-actions">
                          <button type="button" onClick={() => copyToClipboard(driver.api_key, 'API key')} disabled={!driver.api_key}>Copy</button>
                          <button type="button" onClick={() => handleGenerateApiKey(driver)} disabled={savingId === `api-${driver.id}`}>{savingId === `api-${driver.id}` ? 'Generating...' : 'Rotate'}</button>
                        </div>
                      </div>
                    </td>
                    <td>{formatDate(driver.last_active)}</td>
                    <td>
                      <div className="admin-inline-actions">
                        <button type="button" onClick={() => openEditModal(driver)}>Edit</button>
                        <button type="button" onClick={() => openResetModal(driver)}>Reset password</button>
                        <button type="button" onClick={() => setDeleteModalDriver(driver)} className="danger">Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!filteredDrivers.length && (
                  <tr>
                    <td colSpan="7" className="admin-empty-row">No drivers match the current filters.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="admin-panel" id="users">
          <div className="admin-panel-header">
            <div>
              <p className="admin-kicker">Users</p>
              <h3>Registered users</h3>
            </div>
            <span>{filteredUsers.length} users</span>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Role</th>
                  <th>API key</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <strong>{user.full_name}</strong>
                      <span>User #{user.id}</span>
                    </td>
                    <td>{user.email}</td>
                    <td>{user.phone || 'No phone'}</td>
                    <td><Badge tone={user.role === 'admin' ? 'violet' : user.role === 'driver' ? 'blue' : 'neutral'}>{user.role}</Badge></td>
                    <td>
                      <div className="admin-secret-cell">
                        <code>{maskSecret(user.api_key)}</code>
                        <button type="button" onClick={() => copyToClipboard(user.api_key, 'User API key')} disabled={!user.api_key}>Copy</button>
                      </div>
                    </td>
                    <td>{formatDate(user.created_at)}</td>
                  </tr>
                ))}
                {!filteredUsers.length && (
                  <tr>
                    <td colSpan="6" className="admin-empty-row">No users match the current filters.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="admin-panel" id="api-keys">
          <div className="admin-panel-header">
            <div>
              <p className="admin-kicker">GPS devices</p>
              <h3>API key operations</h3>
            </div>
            <span>Use driver keys for GPS ingest and device identity.</span>
          </div>

          <div className="admin-key-grid">
            {filteredDrivers.slice(0, 4).map((driver) => (
              <article className="admin-key-card" key={driver.id}>
                <h4>{driver.driver_name}</h4>
                <p>{maskSecret(driver.api_key)}</p>
                <div className="admin-inline-actions">
                  <button type="button" onClick={() => copyToClipboard(driver.api_key, 'API key')} disabled={!driver.api_key}>Copy</button>
                  <button type="button" onClick={() => handleGenerateApiKey(driver)} disabled={savingId === `api-${driver.id}`}>{savingId === `api-${driver.id}` ? 'Generating...' : 'Rotate'}</button>
                </div>
              </article>
            ))}
          </div>
        </section>
      </main>

      {driverModalOpen && (
        <ModalShell
          title={formMode === 'create' ? 'Create driver' : 'Edit driver'}
          subtitle="Add or update a driver profile and initial login credentials."
          onClose={() => setDriverModalOpen(false)}
        >
          <form className="admin-form" onSubmit={submitDriverForm}>
            <div className="admin-form-grid">
              <label>
                Name
                <input required value={driverForm.driver_name} onChange={(event) => setDriverForm((previous) => ({ ...previous, driver_name: event.target.value }))} />
              </label>
              <label>
                Phone
                <input required value={driverForm.phone} onChange={(event) => setDriverForm((previous) => ({ ...previous, phone: event.target.value }))} />
              </label>
              <label>
                License number
                <input required value={driverForm.license_number} onChange={(event) => setDriverForm((previous) => ({ ...previous, license_number: event.target.value }))} />
              </label>
              <label>
                Status
                <select value={driverForm.status} onChange={(event) => setDriverForm((previous) => ({ ...previous, status: event.target.value }))}>
                  {statusOptions.filter((item) => item !== 'all').map((status) => (
                    <option key={status} value={status}>{status}</option>
                  ))}
                </select>
              </label>
              <label>
                Assigned truck
                <input value={driverForm.assigned_truck} onChange={(event) => setDriverForm((previous) => ({ ...previous, assigned_truck: event.target.value }))} />
              </label>
              <label>
                Truck type
                <input value={driverForm.assigned_truck_type} onChange={(event) => setDriverForm((previous) => ({ ...previous, assigned_truck_type: event.target.value }))} />
              </label>
              <label>
                Rating
                <input type="number" step="0.1" min="0" max="5" value={driverForm.rating} onChange={(event) => setDriverForm((previous) => ({ ...previous, rating: event.target.value }))} />
              </label>
              <label>
                Experience years
                <input type="number" min="0" value={driverForm.experience_years} onChange={(event) => setDriverForm((previous) => ({ ...previous, experience_years: event.target.value }))} />
              </label>
              {formMode === 'create' && (
                <label className="span-two">
                  Password
                  <input required type="password" value={driverForm.password} onChange={(event) => setDriverForm((previous) => ({ ...previous, password: event.target.value }))} placeholder="Minimum 8 characters" />
                </label>
              )}
            </div>

            <div className="admin-modal-actions">
              <button type="button" className="secondary" onClick={() => setDriverModalOpen(false)}>Cancel</button>
              <button type="submit" className="primary" disabled={savingId === 'driver-form'}>{savingId === 'driver-form' ? 'Saving...' : 'Save driver'}</button>
            </div>
          </form>
        </ModalShell>
      )}

      {resetModalDriver && (
        <ModalShell
          title={`Reset password: ${resetModalDriver.driver_name}`}
          subtitle="Enter a new password or leave blank to generate a temporary one."
          onClose={() => setResetModalDriver(null)}
        >
          <form className="admin-form" onSubmit={submitResetPassword}>
            <label>
              New password
              <input type="password" value={resetPasswordValue} onChange={(event) => setResetPasswordValue(event.target.value)} placeholder="Optional for auto-generation" />
            </label>
            <div className="admin-modal-actions">
              <button type="button" className="secondary" onClick={() => setResetModalDriver(null)}>Cancel</button>
              <button type="submit" className="primary" disabled={savingId === `reset-${resetModalDriver.id}`}>{savingId === `reset-${resetModalDriver.id}` ? 'Resetting...' : 'Reset password'}</button>
            </div>
          </form>
        </ModalShell>
      )}

      {deleteModalDriver && (
        <ModalShell
          title={`Delete driver: ${deleteModalDriver.driver_name}`}
          subtitle="This action removes the driver profile from the management list."
          onClose={() => setDeleteModalDriver(null)}
        >
          <p className="admin-confirm-copy">Are you sure you want to delete this driver? This cannot be undone.</p>
          <div className="admin-modal-actions">
            <button type="button" className="secondary" onClick={() => setDeleteModalDriver(null)}>Cancel</button>
            <button type="button" className="danger" onClick={confirmDeleteDriver} disabled={savingId === `delete-${deleteModalDriver.id}`}>{savingId === `delete-${deleteModalDriver.id}` ? 'Deleting...' : 'Delete driver'}</button>
          </div>
        </ModalShell>
      )}
    </div>
  )
}
