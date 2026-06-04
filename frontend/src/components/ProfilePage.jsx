import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

function formatCreatedAt(value) {
  if (!value) {
    return 'Recently'
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return String(value)
  }

  return parsed.toLocaleString()
}

export default function ProfilePage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <section className="profile-shell">
      <div className="profile-shell__glow profile-shell__glow--left" />
      <div className="profile-shell__glow profile-shell__glow--right" />

      <div className="profile-layout">
        <article className="profile-card profile-card--accent">
          <p className="section-kicker">Your account</p>
          <h1>{user?.full_name || 'Transport account'}</h1>
          <p>
            Manage the active session for your logistics dashboard, view the current role, and jump back into shipments or fleet tracking.
          </p>

          <div className="profile-chip-row">
            <span className={`profile-chip profile-chip--${user?.role || 'customer'}`}>{user?.role || 'customer'}</span>
            <span className="profile-chip profile-chip--subtle">{user?.email || 'No email available'}</span>
          </div>
        </article>

        <article className="profile-card">
          <div className="profile-detail-grid">
            <div>
              <span>Full name</span>
              <strong>{user?.full_name || 'Unknown'}</strong>
            </div>
            <div>
              <span>Email</span>
              <strong>{user?.email || 'Unknown'}</strong>
            </div>
            <div>
              <span>Phone</span>
              <strong>{user?.phone || 'Unknown'}</strong>
            </div>
            <div>
              <span>Created</span>
              <strong>{formatCreatedAt(user?.created_at)}</strong>
            </div>
          </div>

          <div className="profile-actions">
            <Link to="/shipments" className="profile-action profile-action--primary">
              Open shipments
            </Link>
            <Link to="/tracking" className="profile-action">
              Track shipments
            </Link>
            {user?.role === 'admin' && (
              <Link to="/admin" className="profile-action">
                Admin dashboard
              </Link>
            )}
            <button type="button" className="profile-action profile-action--danger" onClick={handleLogout}>
              Logout
            </button>
          </div>
        </article>
      </div>
    </section>
  )
}