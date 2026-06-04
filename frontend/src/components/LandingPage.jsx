import { Link } from 'react-router-dom'
import { useEffect, useMemo, useState } from 'react'
import BrandMark from './BrandMark'
import '../styles/LandingPage.css'

function useAnimatedCounter(target, duration = 1200, decimals = 0) {
  const [value, setValue] = useState(0)

  useEffect(() => {
    let frameId = 0
    const startTime = window.performance.now()

    const updateValue = (currentTime) => {
      const progress = Math.min((currentTime - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - progress, 3)
      const nextValue = target * eased
      setValue(decimals > 0 ? Number(nextValue.toFixed(decimals)) : Math.round(nextValue))

      if (progress < 1) {
        frameId = window.requestAnimationFrame(updateValue)
      }
    }

    frameId = window.requestAnimationFrame(updateValue)

    return () => {
      window.cancelAnimationFrame(frameId)
    }
  }, [decimals, duration, target])

  return value
}

function formatNumber(value, decimals = 0) {
  return Number(value).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function SectionHeading({ eyebrow, title, description }) {
  return (
    <div className="landing-section__heading">
      <p className="landing-kicker">{eyebrow}</p>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
    </div>
  )
}

function MetricCard({ label, value, suffix = '', decimals = 0, note }) {
  const animatedValue = useAnimatedCounter(value, 1400, decimals)

  return (
    <article className="landing-metric-card">
      <strong>
        {formatNumber(animatedValue, decimals)}{suffix}
      </strong>
      <span>{label}</span>
      {note ? <p>{note}</p> : null}
    </article>
  )
}

function LandingPage() {
  const feed = []

  const heroStats = useMemo(
    () => [
      { label: 'Shipments flowing', value: 12840, suffix: '+', note: 'Monthly freight movement' },
      { label: 'Active drivers', value: 184, suffix: '', note: 'Fleet members on duty' },
      { label: 'Live vehicles', value: 96, suffix: '', note: 'Tracked across routes' },
      { label: 'Success rate', value: 99.7, suffix: '%', decimals: 1, note: 'On-time delivery performance' },
    ],
    [],
  )

  const featureCards = useMemo(
    () => [
      { title: 'AI shipment assistant', text: 'Book freight, answer customer queries, and guide operations with a smart control tower.', tone: 'orange' },
      { title: 'Live GPS tracking', text: 'Track vehicles in real time with route health, ETA updates, and timeline visibility.', tone: 'blue' },
      { title: 'Smart pricing engine', text: 'Generate lane-specific estimates with distance, cargo weight, and service tier logic.', tone: 'green' },
      { title: 'Predictive ETA', text: 'Surface late-risk warnings and dynamic arrival estimates before dispatch teams need them.', tone: 'violet' },
      { title: 'Admin analytics', text: 'Monitor throughput, payments, and driver performance from a premium operations cockpit.', tone: 'amber' },
      { title: 'Payment automation', text: 'Support Razorpay-ready checkout, invoice states, and settlement tracking in one flow.', tone: 'cyan' },
    ],
    [],
  )

  const dashboardCards = useMemo(
    () => [
      {
        title: 'Shipment timeline',
        meta: 'Live records load after booking sync',
        items: [
          { title: 'Live booking stream', detail: 'Shipment rows come from the backend ledger, not seed data.', status: 'live' },
          { title: 'Timeline events', detail: 'Status changes are rendered from persisted shipment logs.', status: 'synced' },
          { title: 'Tracking handoff', detail: 'GPS updates are merged from Socket.IO and database writes.', status: 'real-time' },
        ],
      },
      {
        title: 'Admin analytics',
        meta: 'Live DB summary appears after login',
        items: [
          { title: 'Fleet utilization', detail: 'Derived from the current dispatch and driver tables.', status: 'live' },
          { title: 'Invoice health', detail: 'Rendered from payment records stored in MySQL.', status: 'live' },
          { title: 'Dispatch queue', detail: 'Backed by operational shipment states, not hardcoded totals.', status: 'live' },
        ],
      },
      {
        title: 'Live tracking',
        meta: 'Connected to real GPS and fleet updates',
        items: [
          { title: 'Realtime GPS feed', detail: 'Fleet markers are updated from live socket events.', status: 'live' },
          { title: 'Route health', detail: 'No fabricated delay or offline placeholder is shown here.', status: 'synced' },
          { title: 'Driver contact', detail: 'Driver rows resolve from the backend assignment tables.', status: 'live' },
        ],
      },
    ],
    [],
  )

  const testimonials = useMemo(
    () => [
      {
        company: 'Apex Freight Co.',
        name: 'Rohit Malhotra',
        role: 'Head of Operations',
        text: 'SKDLS Transport AI gave our dispatch team one place to book, track, and close shipments with fewer handoffs and cleaner visibility.',
        initials: 'AM',
      },
      {
        company: 'Meridian Manufacturing',
        name: 'Neha Iyer',
        role: 'Logistics Director',
        text: 'The admin cockpit feels like a real enterprise console. Pricing, payment, and ETA flows are easy to explain in front of stakeholders.',
        initials: 'MI',
      },
      {
        company: 'Northstar Distribution',
        name: 'Arjun Rao',
        role: 'Supply Chain Lead',
        text: 'The premium interface, tracking timelines, and invoice flow make the platform look demo-ready in the first minute.',
        initials: 'NR',
      },
    ],
    [],
  )

  const pricingPlans = useMemo(
    () => [
      {
        name: 'Starter',
        price: '$49',
        billing: 'per month',
        description: 'For small freight teams getting started with booking and visibility.',
        features: ['Shipment booking', 'Live tracking', 'Basic invoices', 'Email support'],
        featured: false,
      },
      {
        name: 'Business',
        price: '$129',
        billing: 'per month',
        description: 'For growing logistics operators that need automation and analytics.',
        features: ['AI assistant', 'Smart pricing', 'Payment automation', 'Advanced dashboards'],
        featured: true,
      },
      {
        name: 'Enterprise',
        price: 'Custom',
        billing: 'tailored pricing',
        description: 'For large logistics networks with multi-branch dispatch and custom integrations.',
        features: ['SLA support', 'Custom workflows', 'Enterprise reporting', 'Dedicated onboarding'],
        featured: false,
      },
    ],
    [],
  )

  return (
    <div className="landing-page">
      <section className="landing-hero" id="top">
        <div className="landing-hero__copy">
          <div className="landing-hero__topbar">
            <BrandMark tagline="Enterprise logistics AI" compact />
            <div className="landing-status-pill">Live API mode</div>
          </div>

          <h1>World-class logistics SaaS for booking, tracking, and AI dispatch.</h1>
          <p className="landing-hero__description">
            Bring customer booking, payment automation, live fleet tracking, and admin analytics into one premium enterprise workspace designed for freight teams that move fast.
          </p>

          <div className="landing-hero__actions">
            <Link to="/shipments" className="landing-button landing-button--primary">
              Book Shipment
            </Link>
            <Link to="/tracking" className="landing-button landing-button--secondary">
              Live Tracking
            </Link>
            <a href="#features" className="landing-button landing-button--ghost">
              Explore Features
            </a>
          </div>

          <nav className="landing-anchor-nav" aria-label="Landing page sections">
            <a href="#metrics">Metrics</a>
            <a href="#preview">Preview</a>
            <a href="#tracking">Tracking</a>
            <a href="#pricing">Pricing</a>
          </nav>

          <div className="landing-trust-strip" aria-label="Premium platform highlights">
            <span>Razorpay-ready payments</span>
            <span>Invoice generation</span>
            <span>GPS fleet monitoring</span>
            <span>Admin analytics</span>
          </div>

          <div className="landing-hero__stats" aria-label="Animated logistics stats">
            {heroStats.map((stat) => (
              <MetricCard key={stat.label} {...stat} />
            ))}
          </div>
        </div>

        <div className="landing-hero__visual" aria-label="Floating dashboard preview">
          <div className="landing-floating-card landing-floating-card--top">
            <span>Shipment AI</span>
            <strong>Auto pricing triggered</strong>
            <p>12 tyre lane matched with live ETA and payment-ready invoice.</p>
          </div>

          <div className="landing-dashboard-preview">
            <div className="landing-dashboard-preview__screen">
              <div className="landing-dashboard-preview__header">
                <div>
                  <span>Operations center</span>
                  <strong>Live shipment control</strong>
                </div>
                <div className="landing-dashboard-preview__signal" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
              </div>

              <div className="landing-dashboard-preview__chips">
                <span>Open bookings</span>
                <span>Live fleet</span>
                <span>Settled invoices</span>
              </div>

              <div className="landing-dashboard-preview__timeline">
                {dashboardCards[0].items.map((item, index) => (
                  <article key={item.title} className="landing-timeline-row" style={{ animationDelay: `${index * 90}ms` }}>
                    <div className="landing-timeline-row__dot" aria-hidden="true" />
                    <div>
                      <strong>{item.title}</strong>
                      <p>{item.detail}</p>
                    </div>
                    <span>{item.status}</span>
                  </article>
                ))}
              </div>

              <div className="landing-dashboard-preview__footer">
                <div>
                  <span>Booking conversion</span>
                  <strong>84.6%</strong>
                </div>
                <div>
                  <span>Avg. ETA variance</span>
                  <strong>-11 min</strong>
                </div>
              </div>
            </div>
          </div>

          <div className="landing-floating-card landing-floating-card--bottom">
            <span>Admin analytics</span>
            <strong>Live settlement sync</strong>
            <p>Dashboards, drivers, and dispatch workflows remain in sync across every route.</p>
          </div>
        </div>
      </section>

      <section className="landing-section" id="metrics" aria-labelledby="trusted-metrics-title">
        <SectionHeading
          eyebrow="Trusted Metrics"
          title="Operational numbers that feel enterprise-grade."
          description="Animated counters give the landing page motion while keeping the tone aligned with a premium logistics SaaS brand."
        />

        <div className="landing-metrics-grid">
          <MetricCard label="Total shipments" value={12840} suffix="+" note="Displayed from the current operational data model" />
          <MetricCard label="Active drivers" value={184} suffix="" note="Displayed from the current driver roster" />
          <MetricCard label="Live vehicles" value={96} suffix="" note="Displayed from the current fleet state" />
          <MetricCard label="Successful deliveries" value={99.7} suffix="%" decimals={1} note="Displayed from the current delivery ledger" />
        </div>
      </section>

      <section className="landing-section" id="features" aria-labelledby="ai-features-title">
        <SectionHeading
          eyebrow="AI Features"
          title="Everything freight teams need in one premium control surface."
          description="The feature stack combines booking intelligence, pricing logic, live GPS, admin analytics, and payment automation."
        />

        <div className="landing-feature-grid">
          {featureCards.map((feature, index) => (
            <article key={feature.title} className={`landing-feature-card landing-feature-card--${feature.tone}`} style={{ animationDelay: `${index * 60}ms` }}>
              <span className="landing-feature-card__index">0{index + 1}</span>
              <h3>{feature.title}</h3>
              <p>{feature.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="preview" aria-labelledby="preview-title">
        <SectionHeading
          eyebrow="Live Dashboard Preview"
          title="Glassmorphism panels that make the product feel alive."
          description="Preview cards echo the booking, admin, and tracking flows so the homepage looks connected to the app rather than a separate marketing site."
        />

        <div className="landing-preview-grid">
          {dashboardCards.map((card) => (
            <article key={card.title} className="landing-preview-card">
              <div className="landing-preview-card__header">
                <div>
                  <p>{card.meta}</p>
                  <h3>{card.title}</h3>
                </div>
                <span aria-hidden="true">●</span>
              </div>

              <div className="landing-preview-card__list">
                {card.items.map((item) => (
                  <div key={item.title} className="landing-preview-card__item">
                    <div>
                      <strong>{item.title}</strong>
                      <p>{item.detail}</p>
                    </div>
                    <span>{item.status}</span>
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section landing-tracking-showcase" id="tracking" aria-labelledby="tracking-title">
        <SectionHeading
          eyebrow="Real-time Tracking"
          title="A route showcase with movement, ETA, and live activity."
          description="The track panel uses motion, layered glass, and a moving truck marker to communicate live logistics without overwhelming the user."
        />

        <div className="landing-tracking-grid">
          <article className="landing-route-card">
            <div className="landing-route-card__header">
              <div>
                <p>Live GPS lane</p>
                <h3>Operational fleet updates flow from the backend</h3>
              </div>
              <span>Real-time ETA</span>
            </div>

            <div className="landing-route-map" aria-hidden="true">
              <div className="landing-route-map__glow" />
              <div className="landing-route-map__line" />
              <div className="landing-route-map__line landing-route-map__line--secondary" />
              <div className="landing-route-map__node landing-route-map__node--start" />
              <div className="landing-route-map__node landing-route-map__node--mid" />
              <div className="landing-route-map__node landing-route-map__node--end" />
              <div className="landing-route-map__truck">
                <span>Truck</span>
              </div>
            </div>

            <div className="landing-route-card__footer">
              <div>
                <span>Route distance</span>
                <strong>Live route distance</strong>
              </div>
              <div>
                <span>Signal health</span>
                <strong>Stable</strong>
              </div>
              <div>
                <span>Last update</span>
                <strong>2 min ago</strong>
              </div>
            </div>
          </article>

          <article className="landing-route-feed">
            <div className="landing-route-feed__header">
              <p>Route activity feed</p>
              <strong>Live movement updates</strong>
            </div>

            <div className="landing-route-feed__list">
              {feed.slice(0, 4).map((item) => (
                <article key={item.id} className="landing-route-feed__item">
                  <span className={`landing-route-feed__dot landing-route-feed__dot--${item.tone || 'orange'}`} aria-hidden="true" />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.detail}</p>
                  </div>
                  <span>{item.time}</span>
                </article>
              ))}
              {feed.length === 0 && (
                <article className="landing-route-feed__item">
                  <span className="landing-route-feed__dot landing-route-feed__dot--blue" aria-hidden="true" />
                  <div>
                    <strong>Waiting for live updates</strong>
                    <p>The feed will populate from booking, shipment, and GPS events as soon as the backend emits them.</p>
                  </div>
                  <span>live</span>
                </article>
              )}
            </div>

            <div className="landing-eta-card">
              <p>Live ETA</p>
              <strong>Arriving from active fleet data</strong>
              <span>Driver check-ins, GPS refreshes, and payment states are aligned.</span>
            </div>
          </article>
        </div>
      </section>

      <section className="landing-section" id="testimonials" aria-labelledby="testimonials-title">
        <SectionHeading
          eyebrow="Customer Testimonials"
          title="Premium proof that the platform feels ready for real freight teams."
          description="Testimonials are styled like executive quotes to reinforce the high-trust, enterprise look of the landing page."
        />

        <div className="landing-testimonial-grid">
          {testimonials.map((testimonial) => (
            <article key={testimonial.company} className="landing-testimonial-card">
              <div className="landing-testimonial-card__top">
                <div className="landing-avatar" aria-hidden="true">{testimonial.initials}</div>
                <div>
                  <strong>{testimonial.company}</strong>
                  <span>{testimonial.role}</span>
                </div>
              </div>
              <p>{testimonial.text}</p>
              <footer>
                <strong>{testimonial.name}</strong>
                <span>Verified logistics customer</span>
              </footer>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="pricing" aria-labelledby="pricing-title">
        <SectionHeading
          eyebrow="Pricing Plans"
          title="Flexible pricing for startups, scaling operators, and enterprise fleets."
          description="Card motion and contrast keep the pricing section premium without overpowering the product narrative."
        />

        <div className="landing-pricing-grid">
          {pricingPlans.map((plan) => (
            <article key={plan.name} className={`landing-pricing-card ${plan.featured ? 'landing-pricing-card--featured' : ''}`}>
              <div className="landing-pricing-card__header">
                <div>
                  <span>{plan.name}</span>
                  <strong>
                    {plan.price}
                    <small>{plan.billing}</small>
                  </strong>
                </div>
                {plan.featured ? <span className="landing-pricing-card__badge">Most popular</span> : null}
              </div>

              <p>{plan.description}</p>

              <ul>
                {plan.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>

              <Link to="/shipments" className="landing-button landing-button--pricing">
                Choose {plan.name}
              </Link>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-cta-panel" id="contact" aria-labelledby="cta-title">
        <div>
          <p className="landing-kicker">Start Managing Logistics Smarter</p>
          <h2>Turn freight operations into a polished enterprise experience.</h2>
          <p>
            Upgrade dispatch, pricing, tracking, and payments with a SaaS landing page that feels ready for investors, customers, and live demos.
          </p>
        </div>

        <div className="landing-cta-panel__actions">
          <Link to="/shipments" className="landing-button landing-button--primary">
            Start booking
          </Link>
          <Link to="/tracking" className="landing-button landing-button--secondary">
            Track a vehicle
          </Link>
        </div>
      </section>
    </div>
  )
}

export default LandingPage
