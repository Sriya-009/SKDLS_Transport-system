import { useEffect, useRef, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom'
import Chat from './components/Chat'
import Tracking from './components/Tracking'
import { extractDistance } from './utils/transportUtils'
import truckIcon from './assets/truck-launcher.png'
import './App.css'

const navLinks = [
  { name: 'Loading', path: '/loading' },
  { name: 'Unloading', path: '/unloading' },
  { name: 'Transportation', path: '/transportation' },
  { name: 'Tracking', path: '/tracking' },
]

function HomePage() {
  return (
    <section className="hero">
      <h1>Welcome to SKDLS Transportations</h1>
      <p>Your Trusted Shipping Partner</p>
      <Link to="/support" className="hero-chat-launcher" aria-label="Open chatbot">
        <img src={truckIcon} alt="Open chatbot" className="hero-chat-image" />
      </Link>
    </section>
  )
}

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
              <h2>Booking Summary</h2>
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

function App() {
  return (
    <Router>
      <main className="app">
        <nav className="navbar">
          <Link to="/" className="navbar-logo">
            SKDLS Transportations
          </Link>
          <div className="nav-links">
            {navLinks.map((link) => (
              <Link to={link.path} key={link.name} className="nav-link">
                {link.name}
              </Link>
            ))}
          </div>
        </nav>

        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/loading" element={<LoadingPage />} />
          <Route path="/unloading" element={<ServicePage title="Unloading" />} />
          <Route path="/transportation" element={<ServicePage title="Transportation" />} />
          <Route path="/tracking" element={<Tracking />} />
          <Route path="/support" element={<Chat />} />
        </Routes>

        <footer>© 2026 SKDLS Transportations</footer>
      </main>
    </Router>
  )
}

export default App
