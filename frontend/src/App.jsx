import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Link, useLocation, useNavigate } from 'react-router-dom'
import Booking from './components/Booking'
import BrandMark from './components/BrandMark'
import LandingPage from './components/LandingPage'
import { MaintenancePage, NotFoundPage } from './components/SystemPages'
import ProtectedRoute from './components/ProtectedRoute'
import { PageSkeleton, OfflineBanner, OnboardingModal, ToastViewport } from './components/AppChrome'
import { useAuth } from './context/AuthContext'
import { useUi } from './context/UiContext'
import { extractDistance } from './utils/transportUtils'
import './App.css'
import './styles/Auth.css'
import './styles/demo-ready.css'

const Chat = lazy(() => import('./components/Chat'))
const BookingConfirmation = lazy(() => import('./components/BookingConfirmation'))
const AdminDashboard = lazy(() => import('./components/AdminDashboard'))
const Tracking = lazy(() => import('./components/Tracking'))
const AuthPage = lazy(() => import('./components/AuthPage'))
const ProfilePage = lazy(() => import('./components/ProfilePage'))

const navLinks = [
  { name: 'Shipments', path: '/shipments' },
  { name: 'Loading', path: '/loading' },
  { name: 'Unloading', path: '/unloading' },
  { name: 'Transportation', path: '/transportation' },
  { name: 'Tracking', path: '/tracking' },
  { name: 'Admin Dashboard', path: '/admin' },
]

function LoadingPage() {
  const [transportType, setTransportType] = useState('')
  const [source, setSource] = useState('')
  const [destination, setDestination] = useState('')
  const [tons, setTons] = useState('')
  const [result, setResult] = useState('')
  const [distanceMessage, setDistanceMessage] = useState('')
  const [price, setPrice] = useState(null)
  const [showSummary, setShowSummary] = useState(false)
  const distanceCacheRef = useRef(new Map())

  const calculatePrice = (distance) => {
    if (distance < 500) {
      return Math.round(10000 + 40 * distance)
    } else if (distance < 1000) {
      return Math.round(15000 + 40 * distance)
    } else if (distance < 1500) {
      return Math.round(18000 + 40 * distance)
    } else {
      return Math.round(25000 + 50 * distance)
    }
  }

  const getLorryAvailability = (tonsValue) => {
    if (tonsValue >= 20 && tonsValue < 25) return 'Available: 12 Tyre Lorries'
    if (tonsValue >= 25 && tonsValue < 30) return 'Available: 14 Tyre Lorries'
    if (tonsValue >= 30 && tonsValue <= 35) return 'Available: 16 Tyre Lorries'
    return 'Not Available'
  }

  const updateAvailability = (tonsValue) => {
    const parsedTons = Number(tonsValue)

    if (!tonsValue || Number.isNaN(parsedTons)) {
      setResult('')
      setPrice(null)
      return
    }

    setResult(getLorryAvailability(parsedTons))
  }

  const handleTransportTypeChange = (value) => {
    setTransportType(value)
    setSource('')
    setDestination('')
    setTons('')
    setResult('')
    setDistanceMessage('')
    setPrice(null)
    setShowSummary(false)
  }

  const handleSourceChange = (value) => {
    setSource(value)
  }

  const handleDestinationChange = (value) => {
    setDestination(value)
  }

  const handleTonsChange = (value) => {
    setTons(value)
    updateAvailability(value)
  }

  const handleNextClick = () => {
    if (price !== null && result === 'Available: 12 Tyre Lorries') {
      setShowSummary(true)
    }
  }

  const handleBackClick = () => {
    setShowSummary(false)
  }

  useEffect(() => {
    if (transportType !== 'National') {
      setDistanceMessage('')
      setPrice(null)
      return
    }

    const trimmedSource = source.trim()
    const trimmedDestination = destination.trim()

    if (!trimmedSource && !trimmedDestination) {
      setDistanceMessage('')
      setPrice(null)
      return
    }

    if (trimmedSource.length < 3 || trimmedDestination.length < 3) {
      setDistanceMessage('Enter valid locations')
      setPrice(null)
      return
    }

    const normalizedSource = trimmedSource.toLowerCase()
    const normalizedDestination = trimmedDestination.toLowerCase()
    const cacheKey = `${normalizedSource}|${normalizedDestination}`
    const cachedDistance = distanceCacheRef.current.get(cacheKey)

    if (cachedDistance) {
      setDistanceMessage(cachedDistance)
      return
    }

    setDistanceMessage('Calculating distance...')

    const controller = new AbortController()
    let isActive = true

    const timeoutId = window.setTimeout(() => {
      const getCoordinates = async (city) => {
        const response = await fetch(
          `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(city)}&format=json&limit=1`,
          { signal: controller.signal },
        )

        if (!response.ok) {
          throw new Error('Distance not available')
        }

        const data = await response.json()

        if (!Array.isArray(data) || data.length === 0) {
          throw new Error('Invalid location')
        }

        const latitude = Number(data[0].lat)
        const longitude = Number(data[0].lon)

        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
          throw new Error('Invalid location')
        }

        return { latitude, longitude }
      }

      const getRouteDistance = async () => {
        try {
          const [sourceCoords, destinationCoords] = await Promise.all([
            getCoordinates(normalizedSource),
            getCoordinates(normalizedDestination),
          ])

          const response = await fetch(
            `https://router.project-osrm.org/route/v1/driving/${sourceCoords.longitude},${sourceCoords.latitude};${destinationCoords.longitude},${destinationCoords.latitude}?overview=false`,
            { signal: controller.signal },
          )

          if (!response.ok) {
            throw new Error('Distance not available')
          }

          const data = await response.json()

          const routeDistance = data.routes?.[0]?.distance

          if (typeof routeDistance !== 'number') {
            throw new Error('Distance not available')
          }

          const adjusted = Math.round((routeDistance / 1000) * 1.08)

          const nextDistanceMessage = `Estimated Lorry Distance: ${adjusted} km`

          if (isActive) {
            distanceCacheRef.current.set(cacheKey, nextDistanceMessage)
            setDistanceMessage(nextDistanceMessage)

            // Calculate price if it's 12 Tyre lorries
            if (result === 'Available: 12 Tyre Lorries') {
              setPrice(calculatePrice(adjusted))
            } else {
              setPrice(null)
            }
          }
        } catch (error) {
          if (!isActive || controller.signal.aborted) {
            return
          }

          if (error instanceof Error && error.message === 'Invalid location') {
            setDistanceMessage('Invalid location')
            setPrice(null)
            return
          }

          setDistanceMessage('Distance not available')
          setPrice(null)
        }
      }

      getRouteDistance()
    }, 500)

    return () => {
      isActive = false
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [destination, source, transportType, result])

  // Calculate price when result changes and distance is available
  useEffect(() => {
    if (result === 'Available: 12 Tyre Lorries' && distanceMessage.includes('Estimated Lorry Distance')) {
      const extracted = extractDistance(distanceMessage)
      if (extracted !== null) {
        setPrice(calculatePrice(extracted))
      } else {
        setPrice(null)
      }
    } else if (result !== 'Available: 12 Tyre Lorries' && distanceMessage.includes('Estimated Lorry Distance')) {
      setPrice(null)
    }
  }, [result, distanceMessage])

  if (showSummary) {
    return (
      <div className="service-page">
        <h1>Loading Services</h1>
        <div className="service-content">
          <div className="form-card">
            <div className="summary-section">
              <h2>Shipment Summary</h2>
              <div className="summary-content">
                <div className="summary-row">
                  <span className="summary-label">Source:</span>
                  <span className="summary-value">{source}</span>
                </div>
                <div className="summary-row">
                  <span className="summary-label">Destination:</span>
                  <span className="summary-value">{destination}</span>
                </div>
                <div className="summary-row">
                  <span className="summary-label">Lorry Type:</span>
                  <span className="summary-value">12 Tyre</span>
                </div>
                <div className="summary-row">
                  <span className="summary-label">Price:</span>
                  <span className="summary-value price">₹{price}</span>
                </div>
              </div>
              <button type="button" className="back-button" onClick={handleBackClick}>
                Back
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="service-page">
      <h1>Loading Services</h1>
      <div className="service-content">
        <div className="form-card">
          <div className="transport-type-section">
            <label>Select Transport Type</label>
            <div className="transport-type-buttons" role="group" aria-label="Select transport type">
              <button
                type="button"
                className={`transport-type-button ${transportType === 'Local' ? 'active' : ''}`}
                onClick={() => handleTransportTypeChange('Local')}
              >
                Local
              </button>
              <button
                type="button"
                className={`transport-type-button ${transportType === 'National' ? 'active' : ''}`}
                onClick={() => handleTransportTypeChange('National')}
              >
                National
              </button>
            </div>
          </div>

          {transportType === 'National' && (
            <div className="national-form">
              <label htmlFor="source">Source</label>
              <input
                id="source"
                type="text"
                value={source}
                onChange={(event) => handleSourceChange(event.target.value)}
                placeholder="Enter source"
              />

              <label htmlFor="destination">Destination</label>
              <input
                id="destination"
                type="text"
                value={destination}
                onChange={(event) => handleDestinationChange(event.target.value)}
                placeholder="Enter destination"
              />

              <label htmlFor="tons">Tons</label>
              <input
                id="tons"
                type="number"
                value={tons}
                onChange={(event) => handleTonsChange(event.target.value)}
                placeholder="Enter tons"
                min="0"
              />

              {result === 'Not Available' && <p className="availability-result">{result}</p>}
              {distanceMessage && (
                <div className="distance-section">
                  <p className="distance-result">{distanceMessage}</p>
                  {price !== null && result === 'Available: 12 Tyre Lorries' && (
                    <button type="button" className="next-button" onClick={handleNextClick}>
                      Next
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ServicePage({ title }) {
  return (
    <div className="service-page">
      <h1>{title}</h1>
      <div className="service-content">
        <p>Welcome to {title}. We provide professional {title.toLowerCase()} services across the UK.</p>
      </div>
    </div>
  )
}

function AppContent() {
  const { user, ready, logout } = useAuth()
  const { toasts, dismissToast, isOnline, onboardingVisible, completeOnboarding } = useUi()
  const navigate = useNavigate()
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  useEffect(() => {
    const handlePointerDown = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('touchstart', handlePointerDown)

    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('touchstart', handlePointerDown)
    }
  }, [])

  useEffect(() => {
    setMenuOpen(false)
  }, [location.pathname])

  const visibleLinks = navLinks.filter((link) => link.path !== '/admin' || user?.role === 'admin')

  const getInitials = (name) => {
    const parts = String(name || 'User').trim().split(/\s+/).filter(Boolean)
    return parts.slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'U'
  }

  const handleLogout = async () => {
    await logout()
    setMenuOpen(false)
    navigate('/', { replace: true })
  }

  return (
    <main className="app">
      <OfflineBanner isOnline={isOnline} />
      <ToastViewport toasts={toasts} onDismiss={dismissToast} />
      <OnboardingModal open={onboardingVisible} onClose={completeOnboarding} />
      <nav className="navbar">
        <div className="brand-shell">
          <Link to="/" className="navbar-logo" aria-label="SKDLS Transport AI home">
            <BrandMark tagline="Shipment booking, payment, and dispatch" />
          </Link>
        </div>

        <div className="nav-links">
          {visibleLinks.map((link) => (
            <Link to={link.path} key={link.name} className="nav-link">
              {link.name}
            </Link>
          ))}
        </div>

        <div className="nav-actions">
          {ready && !user && (
            <>
              <Link to="/login" className="nav-button nav-button--ghost">
                Login
              </Link>
              <Link to="/signup" className="nav-button nav-button--primary">
                Sign up
              </Link>
            </>
          )}

          {ready && user && (
            <div className="account-menu" ref={menuRef}>
              <button type="button" className="account-toggle" onClick={() => setMenuOpen((previous) => !previous)}>
                <span className="account-avatar">{getInitials(user.full_name)}</span>
                <span className="account-meta">
                  <strong>{user.full_name}</strong>
                  <span>{user.role}</span>
                </span>
              </button>

              {menuOpen && (
                <div className="account-dropdown">
                  <div className="account-dropdown__user">
                    <strong>{user.full_name}</strong>
                    <span>{user.email}</span>
                  </div>

                  <Link to="/profile" className="account-dropdown__link">
                    Profile
                  </Link>
                  <Link to="/shipments" className="account-dropdown__link">
                    Shipments
                  </Link>
                  {user.role === 'admin' && (
                    <Link to="/admin" className="account-dropdown__link">
                      Admin dashboard
                    </Link>
                  )}
                  <button type="button" className="account-dropdown__button account-dropdown__button--danger" onClick={handleLogout}>
                    Logout
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </nav>

      <div key={location.pathname} className="page-transition">
        <Suspense fallback={<PageSkeleton />}>
          <Routes location={location}>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/signup" element={<AuthPage mode="signup" />} />
            <Route path="/loading" element={<LoadingPage />} />
            <Route path="/unloading" element={<ServicePage title="Unloading" />} />
            <Route path="/transportation" element={<ServicePage title="Transportation" />} />
            <Route path="/support" element={<Chat />} />
            <Route path="/maintenance" element={<MaintenancePage />} />
            <Route path="/booking-confirmation/:bookingId" element={<BookingConfirmation />} />

            <Route element={<ProtectedRoute />}>
              <Route path="/shipments" element={<Booking />} />
              <Route path="/tracking" element={<Tracking />} />
              <Route path="/profile" element={<ProfilePage />} />
            </Route>

            <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
              <Route path="/admin" element={<AdminDashboard />} />
            </Route>

            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Suspense>
      </div>

      {location.pathname === '/' ? (
        <footer className="footer-shell footer-shell--landing">
          <div className="footer-brand">
            <BrandMark tagline="Premium logistics SaaS for booking, tracking, and enterprise dispatch." compact />
          </div>

          <div className="landing-footer-columns">
            <div className="landing-footer-column">
              <h3>Company</h3>
              <a href="#top">Homepage</a>
              <a href="#features">Platform</a>
              <a href="#pricing">Pricing</a>
            </div>

            <div className="landing-footer-column">
              <h3>Support</h3>
              <Link to="/support" className="landing-footer-link">AI Assistant</Link>
              <a href="mailto:support@skdlstransport.com">support@skdlstransport.com</a>
              <a href="mailto:ops@skdlstransport.com">ops@skdlstransport.com</a>
            </div>

            <div className="landing-footer-column">
              <h3>Social</h3>
              <div className="landing-footer-socials">
                <a href="https://www.linkedin.com" target="_blank" rel="noreferrer">LinkedIn</a>
                <a href="https://x.com" target="_blank" rel="noreferrer">X</a>
                <a href="https://www.youtube.com" target="_blank" rel="noreferrer">YouTube</a>
              </div>
            </div>
          </div>
        </footer>
      ) : (
        <footer className="footer-shell">
          <div className="footer-brand">
            <span className="footer-mark" aria-hidden="true">SK</span>
            <div className="footer-copy">
              <strong>SKDLS Transportations</strong>
              <span>Premium logistics operations powered by live bookings, tracking, and payments.</span>
            </div>
          </div>

          <div className="footer-actions">
            <span className="footer-pill">Secure JWT</span>
            <span className="footer-pill">Razorpay ready</span>
            <span className="footer-pill">Live fleet sync</span>
          </div>
        </footer>
      )}
    </main>
  )
}

function App() {
  return (
    <Router>
      <AppContent />
    </Router>
  )
}

export default App
