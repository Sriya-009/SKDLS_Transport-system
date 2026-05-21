import { API_BASE_URL } from './apiBase'

/**
 * Send a message to the backend chat API.
 * @param {string} userInput - The user's message
 * @returns {Promise<{ reply: string }>} - Normalized chatbot response
 */
export const sendMessage = async (userInput) => {
  const payload = {
    message: String(userInput ?? '').trim(),
  }

  console.log('[chatService] request payload', payload)

  let res
  try {
    res = await fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
  } catch (error) {
    console.error('[chatService] network error', error)
    throw new Error(`Unable to reach chat service at ${API_BASE_URL}/chat`)
  }

  let data = {}
  const contentType = res.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    data = await res.json()
  } else {
    data = { raw: await res.text() }
  }

  console.log('[chatService] response', { status: res.status, ok: res.ok, data })

  const reply =
    typeof data.reply === 'string'
      ? data.reply
      : typeof data.response === 'string'
        ? data.response
        : typeof data.message === 'string'
          ? data.message
          : ''

  if (!res.ok) {
    const backendError =
      typeof data.error === 'string'
        ? data.error
        : typeof data.message === 'string'
          ? data.message
          : typeof data.raw === 'string'
            ? data.raw
            : ''
    throw new Error(reply || backendError || `Chat API request failed with status ${res.status}`)
  }

  return {
    reply: reply || 'No response received from the chatbot.',
  }
}
