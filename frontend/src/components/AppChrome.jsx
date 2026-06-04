import { Component } from 'react'
import { createPortal } from 'react-dom'
import { ProductionErrorPage } from './SystemPages'

export class AppErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-error-boundary">
          <ProductionErrorPage message={this.state.error?.message || 'Please refresh the page and try again.'} />
        </div>
      )
    }

    return this.props.children
  }
}

export function PageSkeleton({ title = 'Loading logistics workspace', copy = 'Synchronizing live operational data...', rows = 3 }) {
  return (
    <section className="page-surface page-surface--skeleton" aria-busy="true" aria-live="polite">
      <div className="page-skeleton-card">
        <BrandMark tagline={copy} compact loading />
        <div className="skeleton skeleton-title" />
        <p>{title}</p>
        <span>{copy}</span>
        <div className="skeleton-stack">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className="skeleton skeleton-line" />
          ))}
        </div>
      </div>
    </section>
  )
}

export function EmptyState({ title, description, actionLabel, onAction, secondaryLabel, onSecondary, accent = 'amber' }) {
  return (
    <div className={`empty-state empty-state--${accent}`}>
      <div className="empty-state__icon" aria-hidden="true">✦</div>
      <div className="empty-state__content">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <div className="empty-state__actions">
        {actionLabel && onAction && (
          <button type="button" className="app-primary-button" onClick={onAction}>
            {actionLabel}
          </button>
        )}
        {secondaryLabel && onSecondary && (
          <button type="button" className="app-secondary-button" onClick={onSecondary}>
            {secondaryLabel}
          </button>
        )}
      </div>
    </div>
  )
}

export function ToastViewport({ toasts = [], onDismiss }) {
  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="toast-viewport" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <article key={toast.id} className={`toast toast--${toast.type || 'info'}`}>
          <div>
            <strong>{toast.title}</strong>
            <p>{toast.message}</p>
          </div>
          <button type="button" className="toast-dismiss" onClick={() => onDismiss?.(toast.id)}>
            Dismiss
          </button>
        </article>
      ))}
    </div>,
    document.body,
  )
}

export function OfflineBanner({ isOnline }) {
  if (isOnline) {
    return null
  }

  return <div className="offline-banner">Offline mode active. Cached live views remain available until the connection returns.</div>
}

export function OnboardingModal({ open, onClose }) {
  if (!open) {
    return null
  }

  const steps = [
    'Create a shipment and get instant pricing.',
    'Track live status, driver assignment, and invoice delivery.',
    'Review live operational data after the first sync completes.',
  ]

  return (
    <div className="onboarding-backdrop" role="presentation">
      <section className="onboarding-modal" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
        <p className="section-kicker">First-time walkthrough</p>
        <h2 id="onboarding-title">Live logistics controls</h2>
        <p className="onboarding-copy">A quick guided overview helps new visitors understand the booking, payment, and tracking flow in under a minute.</p>
        <ol className="onboarding-steps">
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <div className="onboarding-actions">
          <button type="button" className="app-secondary-button" onClick={onClose}>
            Skip
          </button>
          <button type="button" className="app-primary-button" onClick={onClose}>
            Continue
          </button>
        </div>
      </section>
    </div>
  )
}