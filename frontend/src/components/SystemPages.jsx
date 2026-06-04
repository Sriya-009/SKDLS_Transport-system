import { Link } from 'react-router-dom'
import BrandMark from './BrandMark'

function SystemFrame({ title, eyebrow, description, actionLabel, actionTo = '/', actionSecondaryLabel, actionSecondaryTo = '/support' }) {
  return (
    <main className="system-page">
      <section className="system-page__card">
        <BrandMark tagline="Premium logistics operations" compact />
        <p className="section-kicker">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="system-page__copy">{description}</p>
        <div className="system-page__actions">
          {actionLabel ? (
            <Link to={actionTo} className="app-primary-button">
              {actionLabel}
            </Link>
          ) : null}
          {actionSecondaryLabel ? (
            <Link to={actionSecondaryTo} className="app-secondary-button">
              {actionSecondaryLabel}
            </Link>
          ) : null}
        </div>
      </section>
    </main>
  )
}

export function NotFoundPage() {
  return (
    <SystemFrame
      eyebrow="404 Not Found"
      title="That logistics route does not exist."
      description="The page you requested is unavailable, but the fleet control center and shipment booking flows are still ready."
      actionLabel="Return home"
      actionTo="/"
      actionSecondaryLabel="Open tracking"
      actionSecondaryTo="/tracking"
    />
  )
}

export function MaintenancePage() {
  return (
    <SystemFrame
      eyebrow="Maintenance"
      title="We are refining the logistics platform for deployment."
      description="The service is temporarily unavailable while deployment or maintenance work completes. Please check back shortly."
      actionLabel="Return home"
      actionTo="/"
      actionSecondaryLabel="Contact support"
      actionSecondaryTo="/support"
    />
  )
}

export function ProductionErrorPage({ message = 'The application could not render the requested workspace.' }) {
  return (
    <main className="system-page">
      <section className="system-page__card">
        <BrandMark tagline="Premium logistics operations" compact />
        <p className="section-kicker">Application error</p>
        <h1>We could not render the logistics dashboard.</h1>
        <p className="system-page__copy">{message}</p>
        <div className="system-page__actions">
          <button type="button" className="app-primary-button" onClick={() => window.location.reload()}>
            Reload application
          </button>
          <Link to="/" className="app-secondary-button">
            Return home
          </Link>
        </div>
      </section>
    </main>
  )
}
