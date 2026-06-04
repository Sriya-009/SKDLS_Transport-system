const DEFAULT_API_BASE_URL = 'http://34.200.212.3/api'

const trimTrailingSlash = (value) => value.replace(/\/+$/, '')
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()

export const API_BASE_URL = trimTrailingSlash(configuredBaseUrl || DEFAULT_API_BASE_URL)

const configuredSocketUrl = import.meta.env.VITE_SOCKET_URL?.trim()

const deriveSocketBaseUrl = () => {
  if (configuredSocketUrl) {
    return trimTrailingSlash(configuredSocketUrl)
  }

  if (!API_BASE_URL || API_BASE_URL.startsWith('/')) {
    return undefined
  }

  try {
    const url = new URL(API_BASE_URL)
    url.pathname = url.pathname.replace(/\/api\/?$/, '') || '/'
    url.search = ''
    url.hash = ''

    const path = trimTrailingSlash(url.pathname)
    return `${url.origin}${path && path !== '/' ? path : ''}`
  } catch {
    return undefined
  }
}

export const SOCKET_BASE_URL = deriveSocketBaseUrl()
