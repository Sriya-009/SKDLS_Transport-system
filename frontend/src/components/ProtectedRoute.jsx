import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function ProtectedRoute({ allowedRoles = [] }) {
  const location = useLocation()
  const { ready, user } = useAuth()

  if (!ready) {
    return (
      <section className="route-status-shell">
        <div className="route-status-card">
          <p className="route-status-kicker">Authentication</p>
          <h2>Checking account access</h2>
          <p>Loading your session so the dashboard can decide where to send you.</p>
        </div>
      </section>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  if (allowedRoles.length > 0 && !allowedRoles.includes(user.role)) {
    return <Navigate to="/profile" replace />
  }

  return <Outlet />
}