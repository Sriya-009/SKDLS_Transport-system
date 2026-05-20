import { useState, useRef, useEffect } from 'react'
import { sendMessage } from '../services/chatService'
import { calculateDistance, calculatePrice, extractDistance } from '../utils/transportUtils'
import './Chat.css'

export default function Chat({ source = '', destination = '', distanceMessage = '' }) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [assistantDockOpen, setAssistantDockOpen] = useState(false)
  const [distance, setDistance] = useState(null)
  const [price, setPrice] = useState(null)
  const [chatSource, setChatSource] = useState(null)
  const [chatDestination, setChatDestination] = useState(null)
  const [calculatingDistance, setCalculatingDistance] = useState(false)
  const [pendingMessage, setPendingMessage] = useState(null) // legacy
  const [expectingTons, setExpectingTons] = useState(false)
  const [pendingTons, setPendingTons] = useState(null)
  const [awaitingBookingDecision, setAwaitingBookingDecision] = useState(false)
  const [isRestartingAfterBooking, setIsRestartingAfterBooking] = useState(false)
  const [isFirstMessage, setIsFirstMessage] = useState(true)
  const messagesEndRef = useRef(null)
  const userIdRef = useRef(null)
  const isSendingRef = useRef(false)
  const welcomeShownRef = useRef(false)
  const [welcomeLoading, setWelcomeLoading] = useState(true)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const getTruckTypeFromTons = (tonsValue) => {
    if (tonsValue >= 20 && tonsValue <= 24) return '12 tyre'
    if (tonsValue >= 25 && tonsValue <= 29) return '14 tyre'
    if (tonsValue >= 30 && tonsValue <= 35) return '16 tyre'
    return ''
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  // Initialize persistent user_id and fetch welcome once
  useEffect(() => {
    if (!userIdRef.current) {
      let id = localStorage.getItem('chat_user_id')
      if (!id) {
        id = `user_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
        localStorage.setItem('chat_user_id', id)
      }
      userIdRef.current = id
    }

    if (welcomeShownRef.current) return
    welcomeShownRef.current = true

    let mounted = true
    const fetchWelcome = async () => {
      try {
        const res = await fetch(`http://127.0.0.1:5000/chat/welcome?user_id=${userIdRef.current}`)
        if (!mounted) return
        if (!res.ok) throw new Error('non-200')
        const data = await res.json()
        const welcome = typeof data.reply === 'string' ? data.reply : ''
        if (welcome) {
          // Only append if there are no messages yet to avoid duplicates
          setMessages((prev) => (prev.length === 0 ? [{ id: `welcome-${Date.now()}`, text: welcome, sender: 'bot' }] : prev))
          setIsFirstMessage(false)
          setWelcomeLoading(false)
          return
        }
      } catch (err) {
        if (!mounted) return
        setMessages((prev) => (prev.length === 0 ? [{ id: `welcome-fallback-${Date.now()}`, text: 'Hello! Welcome to SKDLS Transportations.', sender: 'bot' }] : prev))
        setIsFirstMessage(false)
        setWelcomeLoading(false)
      }
    }

    fetchWelcome()
    return () => {
      mounted = false
    }
  }, [])

  const appendMessage = (text, sender, options = {}) => {
    const nextMessage = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      text,
      sender,
      variant: options.variant || 'default',
    }

    setMessages((prev) => [...prev, nextMessage])
  }

  const beginSendLock = () => {
    if (isSendingRef.current) {
      return false
    }

    isSendingRef.current = true
    setIsSending(true)
    return true
  }

  const endSendLock = () => {
    isSendingRef.current = false
    setIsSending(false)
  }

  const summarySource = chatSource || source || ''
  const summaryDestination = chatDestination || destination || ''
  const summaryWeight = pendingTons !== null ? `${pendingTons} tons` : expectingTons ? 'Awaiting load weight' : ''
  const summaryTyreType = pendingTons !== null ? getTruckTypeFromTons(Number(pendingTons)) || 'Pending' : ''
  const summaryDistance = Number.isFinite(distance) ? `${Number(distance)} km` : distanceMessage || 'Awaiting route estimate'
  const summaryPrice = Number.isFinite(price) ? `₹${Number(price)}` : 'Pending'

  const bookingSteps = [
    { label: 'Source', active: Boolean(summarySource) },
    { label: 'Destination', active: Boolean(summaryDestination) },
    { label: 'Weight', active: Boolean(summaryWeight && !summaryWeight.includes('Awaiting')) },
    { label: 'Quote', active: Number.isFinite(distance) || Number.isFinite(price) },
    { label: 'Confirmation', active: awaitingBookingDecision },
  ]

  const bookingProgress = Math.round((bookingSteps.filter((step) => step.active).length / bookingSteps.length) * 100)
  const showBookingSummary = Boolean(summarySource || summaryDestination || summaryWeight || Number.isFinite(distance) || Number.isFinite(price) || awaitingBookingDecision || expectingTons)

  // Calculate distance and price when both source and destination are available
  useEffect(() => {
    if (!chatSource?.trim() || !chatDestination?.trim()) {
      return
    }

    const calculateTransportValues = async () => {
      setCalculatingDistance(true)
      try {
        if (process.env.NODE_ENV !== 'production') {
          console.log(`📍 Calculating distance from "${chatSource}" to "${chatDestination}"...`)
        }
        const calculatedDistance = await calculateDistance(chatSource, chatDestination)
        if (process.env.NODE_ENV !== 'production') {
          console.log(`✓ Distance calculated: ${calculatedDistance} km`)
        }

        // only set distance here — do NOT calculate price until tons provided
        setDistance(calculatedDistance)
        setPrice(null)
      } catch (error) {
        console.error('❌ Error calculating distance/price:', error)
        setDistance(null)
        setPrice(null)
      } finally {
        setCalculatingDistance(false)
      }
    }

    calculateTransportValues()
  }, [chatSource, chatDestination])

  // When pendingTons is set and distance is available, compute price and send final payload
  useEffect(() => {
    const sendFinal = async () => {
      if (pendingTons === null) return
      if (calculatingDistance) return
      if (distance === null || !Number.isFinite(distance)) {
        // wait until distance is available
        return
      }

      const tonsVal = Number(pendingTons)
      const truckType = getTruckTypeFromTons(tonsVal)

      if (!truckType) {
        appendMessage('No lorry available for the provided tons.', 'bot')
        setPendingTons(null)
        setLoading(false)
        endSendLock()
        return
      }

      const finalPrice = calculatePrice(distance)

      // Build final payload and send to backend
      try {
        const userId = userIdRef.current || undefined
        const payload = {
          // send the destination as the message so backend records it as destination step
          message: chatDestination || '',
          source: chatSource || '',
          destination: chatDestination || '',
          tons: tonsVal,
          truck_type: truckType,
          distance: Number(distance),
          price: Number(finalPrice),
          ...(userId ? { user_id: userId } : {}),
        }

        if (process.env.NODE_ENV !== 'production') {
          console.log('📤 Final payload to backend:', payload)
        }

        const res = await fetch('http://127.0.0.1:5000/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })

        let data = {}
        const ct = res.headers.get('content-type') || ''
        if (ct.includes('application/json')) data = await res.json()

        // Prefer structured error or confirmation responses from backend
        if (data && typeof data.error === 'string') {
          appendMessage(data.error, 'bot')
        } else if (data && data.status === 'confirmed') {
          const d = data.distance || distance
          const p = data.price || finalPrice
          const l = data.lorryType || ''
          appendMessage(`Distance: ${d} km\nEstimated Price: ₹${p}\nLorry Type: ${l}`, 'bot', { variant: 'summary' })
          // Reset state after successful booking
          setChatSource(null)
          setChatDestination(null)
          setDistance(null)
          setPrice(null)
          setExpectingTons(false)
          setPendingTons(null)
          // Append confirmation and wait for yes/no decision
          appendMessage('Booking confirmed! Do you want to book another transport?', 'bot')
          setAwaitingBookingDecision(true)
        } else {
          const reply = typeof data.reply === 'string' ? data.reply : ''
          appendMessage(reply || 'Received response from server.', 'bot')
        }
      } catch (err) {
        console.error('❌ Error sending final payload:', err)
        appendMessage('Error: could not complete booking. Try again later.', 'bot')
      } finally {
        setPendingTons(null)
        setLoading(false)
        endSendLock()
      }
    }

    sendFinal()
  }, [pendingTons, calculatingDistance, distance])

  const sendPendingMessage = async () => {
    if (!pendingMessage) return

    try {
      const { userInput } = pendingMessage
      const distanceVal = distance !== null && Number.isFinite(distance) ? Number(distance) : null
      const priceVal = price !== null && Number.isFinite(price) ? Number(price) : null

      if (process.env.NODE_ENV !== 'production') {
        console.log('📤 Sending pending destination message')
        console.log('   distance:', distanceVal, 'price:', priceVal)
      }

      const response = await sendMessage(userInput, distanceVal, priceVal)
      appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot')
    } catch (error) {
      console.error('❌ Failed to send pending message:', error)
      appendMessage('Error: Could not reach the chat service. Please try again.', 'bot')
    } finally {
      setLoading(false)
      setPendingMessage(null)
    }
  }

  const handleSendMessage = async () => {
    const userInput = input.trim()
    if (!userInput || loading || isSendingRef.current) return

    if (!beginSendLock()) return

    // Add user message to chat
    appendMessage(userInput, 'user')
    setInput('')
    setLoading(true)
    let keepSendLock = false

    try {
      // Handle booking decision (yes/no for another transport)
      if (awaitingBookingDecision) {
        const userSaysYes = userInput.toLowerCase().includes('yes')
        const userSaysNo = userInput.toLowerCase().includes('no')
        const response = await sendMessage(userInput, null, null)

        if (userSaysYes) {
          // User wants to book another transport
          setAwaitingBookingDecision(false)
          setIsRestartingAfterBooking(true)
          // Don't append greeting if isRestartingAfterBooking is true
          if (response?.reply && !response.reply.includes('Hi 👋')) {
            appendMessage(response.reply, 'bot')
          } else if (response?.reply && response.reply.includes('Hi 👋')) {
            // Skip the greeting on restart
            setIsRestartingAfterBooking(false)
          }
          return
        } else if (userSaysNo) {
          // User ends the conversation
          setAwaitingBookingDecision(false)
          appendMessage(response?.reply || 'Thank you for using our service! Have a great day.', 'bot')
          return
        } else {
          // User said something else, still waiting for yes/no
          appendMessage(response?.reply || 'Please reply with "yes" to book another transport or "no" to exit.', 'bot')
          return
        }
      }

      // Get the last bot message to determine context
      const lastBotMessage = messages.filter((m) => m.sender === 'bot').pop()?.text || ''

      // If the last message asked for source, store this as source
      if (lastBotMessage.includes('Please enter your source location')) {
        setChatSource(userInput)
        // Immediately send the message (source doesn't need distance calculation)
        const response = await sendMessage(userInput, null, null)
        appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot')
      }
      // If the last message asked for destination, store this and prompt for tons
      else if (lastBotMessage.includes('Enter your destination')) {
        setChatDestination(userInput)
        const response = await sendMessage(userInput, null, null)
        // Prompt the user for tons before calculating price
        appendMessage(response?.reply || 'Enter tons', 'bot')
        setExpectingTons(true)
      }
      // If we are expecting tons, process tons input
      else if (expectingTons) {
        const tonsVal = Number(userInput)
        const truckType = getTruckTypeFromTons(tonsVal)
        if (!Number.isFinite(tonsVal)) {
          const response = await sendMessage(userInput, null, null)
          appendMessage(response?.reply || 'Please enter a valid tons number (e.g., 22)', 'bot')
        } else if (!truckType) {
          const response = await sendMessage(userInput, null, null)
          appendMessage(response?.reply || 'No lorry available for the provided tons.', 'bot')
        } else {
          // store pending tons and wait for distance (if needed) to send final payload
          setPendingTons(tonsVal)
          setExpectingTons(false)
          keepSendLock = true
          // keep loading true until final payload completes
        }
      } else {
        // For other messages (like vehicle type), send directly
        const response = await sendMessage(userInput, null, null)
        
        // Check if response is greeting and handle suppression
        const isGreeting = response?.reply?.includes('Hi 👋')
        if (isGreeting && isRestartingAfterBooking) {
          // Skip the greeting on restart
          setIsRestartingAfterBooking(false)
          // Continue to vehicle type step (which is implicit in the greeting response)
        } else if (isGreeting && isFirstMessage) {
          // Show greeting only on first message
          appendMessage(response.reply, 'bot')
          setIsFirstMessage(false)
        } else {
          appendMessage(response?.reply || 'Sorry, I could not process your request.', 'bot')
        }
      }
    } catch (error) {
      console.error('❌ Failed to send message:', error)
      appendMessage('Error: Could not reach the chat service. Please try again.', 'bot')
      setPendingMessage(null)
    } finally {
      if (!keepSendLock) {
        setLoading(false)
        endSendLock()
      }
    }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (loading || isSending) {
        return
      }
      handleSendMessage()
    }
  }

  const handleAssistantDockToggle = () => {
    setAssistantDockOpen((prev) => !prev)
  }

  return (
    <div className="chat-page">
      <div className="chat-container">
        <header className="chat-header">
          <div className="chat-brand">
            <div className="chat-brand-mark" aria-hidden="true">
              🚛
            </div>
            <div>
              <p className="chat-eyebrow">Logistics Control Tower</p>
              <h2>SKDLS Transport Assistant</h2>
            </div>
          </div>

          <div className="chat-header-status">
            <span className="status-pill status-pill-online">
              <span className="status-dot" />
              Online
            </span>
            <span className="status-pill">GPS Live</span>
            <span className="status-pill">DB Connected</span>
          </div>
        </header>

        <div className="chat-workspace">
          {showBookingSummary && (
            <aside className="booking-summary-card" aria-label="Booking summary">
              <div className="summary-card-header">
                <div>
                  <p className="summary-kicker">Current booking</p>
                  <h3>Transport Summary</h3>
                </div>
                <div className="summary-route-chip">Live route</div>
              </div>

              <div className="summary-grid">
                <div className="summary-item">
                  <span className="summary-label">Source</span>
                  <strong>{summarySource || 'Awaiting source'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Destination</span>
                  <strong>{summaryDestination || 'Awaiting destination'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Weight</span>
                  <strong>{summaryWeight || 'Awaiting weight'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Tyre type</span>
                  <strong>{summaryTyreType || 'Pending'}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Distance</span>
                  <strong>{summaryDistance}</strong>
                </div>
                <div className="summary-item">
                  <span className="summary-label">Estimate</span>
                  <strong>{summaryPrice}</strong>
                </div>
              </div>

              <div className="summary-progress-block">
                <div className="summary-progress-header">
                  <span>Booking progress</span>
                  <strong>{bookingProgress}%</strong>
                </div>
                <div className="summary-progress-bar" aria-hidden="true">
                  <span style={{ width: `${bookingProgress}%` }} />
                </div>
                <div className="summary-steps" aria-label="Booking steps">
                  {bookingSteps.map((step) => (
                    <span key={step.label} className={`summary-step ${step.active ? 'is-active' : ''}`}>
                      {step.label}
                    </span>
                  ))}
                </div>
              </div>
            </aside>
          )}

          <section className="chat-thread">
            <div className="chat-messages">
              {welcomeLoading && (
                <div className="welcome-loading">
                  <span className="typing-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  Connecting to transport assistant...
                </div>
              )}
              {/* Welcome handled by backend; placeholder removed to avoid duplicate messages */}

              {messages.map((msg) => (
                <div key={msg.id} className={`message message-${msg.sender}`}>
                  <div className={`message-bubble ${msg.variant === 'summary' ? 'message-summary' : ''}`}>
                    {msg.text}
                  </div>
                </div>
              ))}

              {loading && (
                <div className="message message-bot">
                  <div className="message-bubble loading-indicator">
                    <span className="typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                    Typing...
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            <div className="chat-input-area">
              <div className="input-shell">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Type your next transport request..."
                  disabled={loading || isSending}
                  rows="3"
                />
                <div className="input-hint-row">
                  <span>One message at a time keeps booking steps accurate.</span>
                  <span>{loading || isSending ? 'Assistant processing' : 'Ready'}</span>
                </div>
              </div>
              <button onClick={handleSendMessage} disabled={loading || isSending || !input.trim()} className="send-button">
                {loading || isSending ? (
                  <span className="button-loading">
                    <span className="spinner" />
                    Sending...
                  </span>
                ) : (
                  <span className="button-content">
                    <span className="send-icon" aria-hidden="true">
                      ↑
                    </span>
                    Send
                  </span>
                )}
              </button>
            </div>
          </section>
        </div>
      </div>

      <button type="button" className={`assistant-fab ${assistantDockOpen ? 'is-open' : ''}`} onClick={handleAssistantDockToggle} aria-label="Toggle assistant dock" aria-pressed={assistantDockOpen}>
        <span className="assistant-fab-icon" aria-hidden="true">
          🚚
        </span>
      </button>

      <div className={`assistant-dock ${assistantDockOpen ? 'is-open' : ''}`} aria-hidden={!assistantDockOpen}>
        <div className="assistant-dock-header">
          <strong>Assistant panel</strong>
          <span>Live</span>
        </div>
        <p>Tracking booking flow, GPS updates, and transport replies in a single workspace.</p>
      </div>
    </div>
  )
}
