/**
 * Send a message to the backend chat API with distance and price information
 * @param {string} userInput - The user's message
 * @param {number} calculatedDistance - The calculated distance
 * @param {number} calculatedPrice - The calculated price
 * @returns {Promise<{ reply: string }>} - Normalized chatbot response
 */
export const sendMessage = async (userInput, calculatedDistance, calculatedPrice) => {
  // Generate or retrieve a persistent user_id for session management
  let userId = localStorage.getItem('chat_user_id')
  if (!userId) {
    userId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
    localStorage.setItem('chat_user_id', userId)
  }

  const payload = {
    message: String(userInput ?? '').trim(),
    distance: calculatedDistance === null || calculatedDistance === undefined ? null : Number(calculatedDistance),
    price: calculatedPrice === null || calculatedPrice === undefined ? null : Number(calculatedPrice),
    user_id: userId, // Include user_id for proper session management
  }

  const res = await fetch('http://127.0.0.1:5000/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  let data = {}
  const contentType = res.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    data = await res.json()
  }

  const reply =
    typeof data.reply === 'string'
      ? data.reply
      : typeof data.response === 'string'
        ? data.response
        : typeof data.message === 'string'
          ? data.message
          : ''

  if (!res.ok) {
    throw new Error(reply || `Chat API request failed with status ${res.status}`)
  }

  return {
    reply: reply || 'No response received from the chatbot.',
  }
}
