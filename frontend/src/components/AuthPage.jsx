import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

const initialSignupState = {
  full_name: '',
  email: '',
  phone: '',
  password: '',
  role: 'customer',
}

const initialLoginState = {
  email: '',
  password: '',
}

export default function AuthPage({ mode = 'login' }) {
  const isSignup = mode === 'signup'
  const [formState, setFormState] = useState(isSignup ? initialSignupState : initialLoginState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const { user, ready, login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const fromPath = location.state?.from?.pathname || ''

  const destination = useMemo(() => {
    if (fromPath) {
      return fromPath
    }

    return user?.role === 'admin' ? '/admin' : '/profile'
  }, [fromPath, user?.role])

  useEffect(() => {
    if (ready && user) {
      navigate(destination, { replace: true })
    }
  }, [destination, navigate, ready, user])

  useEffect(() => {
    setFormState(isSignup ? initialSignupState : initialLoginState)
    setError('')
  }, [isSignup])

  const handleChange = (event) => {
    const { name, value } = event.target
    setFormState((previous) => ({
      ...previous,
      [name]: value,
    }))
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    setLoading(true)
    setError('')

    try {
      const payload = isSignup
        ? {
            full_name: formState.full_name,
            email: formState.email,
            phone: formState.phone,
            password: formState.password,
            role: formState.role,
          }
        : {
            email: formState.email,
            password: formState.password,
          }

      const response = isSignup ? await register(payload) : await login(payload)
      const nextPath = fromPath || (response.user?.role === 'admin' ? '/admin' : '/profile')
      navigate(nextPath, { replace: true })
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to complete authentication')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="auth-shell">
      <div className="auth-shell__glow auth-shell__glow--left" />
      <div className="auth-shell__glow auth-shell__glow--right" />

      <div className="auth-grid">
        <div className="auth-hero-card">
          <p className="section-kicker">Secure account access</p>
          <h1>{isSignup ? 'Create a logistics account that travels with your fleet' : 'Sign in to your transport control room'}</h1>
          <p>
            Keep shipments, tracking, and admin operations under one secure JWT session with role-aware access for customers, drivers, and administrators.
          </p>

          <div className="auth-highlight-list">
            <article>
              <strong>JWT cookies</strong>
              <span>Secure, browser-managed sessions without local token storage.</span>
            </article>
            <article>
              <strong>Role aware</strong>
              <span>Customer, admin, and driver access stays on the right side of each route.</span>
            </article>
            <article>
              <strong>Production ready</strong>
              <span>Works with Gunicorn, Nginx, and the existing /api proxy layout.</span>
            </article>
          </div>
        </div>

        <div className="auth-form-card">
          <div className="auth-form-heading">
            <p className="section-kicker">{isSignup ? 'Create account' : 'Welcome back'}</p>
            <h2>{isSignup ? 'Sign up' : 'Login'}</h2>
          </div>

          <form className="auth-form" onSubmit={handleSubmit}>
            {isSignup && (
              <label>
                Full name
                <input
                  type="text"
                  name="full_name"
                  value={formState.full_name}
                  onChange={handleChange}
                  placeholder="Operations Manager"
                  minLength={3}
                  required
                />
              </label>
            )}

            <label>
              Email address
              <input
                type="email"
                name="email"
                value={formState.email}
                onChange={handleChange}
                placeholder="name@company.com"
                autoComplete="email"
                required
              />
            </label>

            {isSignup && (
              <label>
                Phone number
                <input
                  type="tel"
                  name="phone"
                  value={formState.phone}
                  onChange={handleChange}
                  placeholder="+91 98765 43210"
                  autoComplete="tel"
                  required
                />
              </label>
            )}

            <label>
              Password
              <input
                type="password"
                name="password"
                value={formState.password}
                onChange={handleChange}
                placeholder="At least 8 characters"
                minLength={8}
                autoComplete={isSignup ? 'new-password' : 'current-password'}
                required
              />
            </label>

            {isSignup && (
              <label>
                Account role
                <select name="role" value={formState.role} onChange={handleChange}>
                  <option value="customer">Customer</option>
                  <option value="driver">Driver</option>
                  <option value="admin">Admin</option>
                </select>
              </label>
            )}

            {error && <div className="auth-error">{error}</div>}

            <button type="submit" className="auth-submit" disabled={loading}>
              {loading ? 'Processing...' : isSignup ? 'Create account' : 'Login'}
            </button>
          </form>

          <div className="auth-switcher">
            <span>{isSignup ? 'Already have an account?' : 'New to the platform?'}</span>
            <Link to={isSignup ? '/login' : '/signup'} className="auth-switch-link">
              {isSignup ? 'Login now' : 'Create an account'}
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}