import { API_BASE_URL } from './apiBase'

const RETRYABLE_STATUS_CODES = new Set([408, 425, 429, 500, 502, 503, 504])

function sleep(durationMs) {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, durationMs)
  })
}

export class HttpError extends Error {
  constructor(message, { status = 0, retryable = false, offline = false, payload = null } = {}) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.retryable = retryable
    this.offline = offline
    this.payload = payload
  }
}

function parseResponseBody(response) {
  const contentType = response.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    return response.json()
  }

  return response.text().then((text) => ({ message: text }))
}

function readCookie(name) {
  if (typeof document === 'undefined') {
    return ''
  }

  const encodedName = `${encodeURIComponent(name)}=`
  const cookieParts = document.cookie.split(';')

  for (const part of cookieParts) {
    const trimmed = part.trim()
    if (trimmed.startsWith(encodedName)) {
      return decodeURIComponent(trimmed.slice(encodedName.length))
    }
  }

  return ''
}

function buildHeaders(method, headers = {}, body, path = '') {
  const nextHeaders = { ...headers }

  if (body !== undefined) {
    nextHeaders['Content-Type'] = nextHeaders['Content-Type'] || 'application/json'
  }

  if (!['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase())) {
    const csrfToken = readCookie(String(path).includes('/refresh') ? 'csrf_refresh_token' : 'csrf_access_token')
    if (csrfToken) {
      nextHeaders['X-CSRF-TOKEN'] = csrfToken
    }
  }

  return nextHeaders
}

export async function requestJson(path, { method = 'GET', body, headers = {}, credentials = 'include', retries = 2 } = {}) {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    throw new HttpError('You are offline. Reconnect to continue.', {
      offline: true,
      retryable: true,
    })
  }

  const requestInit = {
    method,
    credentials,
    headers: buildHeaders(method, headers, body, path),
    body: body === undefined ? undefined : JSON.stringify(body),
  }

  let lastError = null

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, requestInit)
      const data = await parseResponseBody(response)

      if (!response.ok) {
        const message = data.message || data.error || 'Request failed'
        const retryable = RETRYABLE_STATUS_CODES.has(response.status)

        if (response.status === 401 && !String(path).includes('/auth/refresh') && !String(path).includes('/auth/login') && attempt === 0) {
          try {
            await requestJson('/auth/refresh', { method: 'POST', retries: 0 })
            requestInit.headers = buildHeaders(method, headers, body, path)
            continue
          } catch {
            // Fall through to the original auth error.
          }
        }

        if (retryable && attempt < retries) {
          await sleep(250 * (attempt + 1))
          continue
        }

        throw new HttpError(message, {
          status: response.status,
          retryable,
          payload: data,
        })
      }

      return data
    } catch (error) {
      if (error instanceof HttpError) {
        lastError = error
        if (error.retryable && attempt < retries) {
          await sleep(250 * (attempt + 1))
          continue
        }
        throw error
      }

      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      const wrapped = new HttpError(offline ? 'You are offline. Reconnect to continue.' : 'Network request failed. Retrying may help.', {
        retryable: true,
        offline,
      })
      lastError = wrapped

      if (attempt < retries) {
        await sleep(250 * (attempt + 1))
        continue
      }

      throw wrapped
    }
  }

  throw lastError || new HttpError('Request failed')
}
