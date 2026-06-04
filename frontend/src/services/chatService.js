import { requestJson } from './http'

/**
 * Send a message to the backend chat API.
 * @param {string} userInput - The user's message
 * @returns {Promise<{ reply: string, message: string, action: string, card_type: string, intent: string, confidence: number, workflow: string, route: string, type: string, data: object, suggestions: string[], status: string }>} - Normalized chatbot response
 */
export const sendMessage = async (userInput) => {
  const payload = {
    message: String(userInput ?? '').trim(),
  }

  const data = await requestJson('/chat', {
    method: 'POST',
    body: payload,
  })

  const reply =
    typeof data.reply === 'string'
      ? data.reply
      : typeof data.response === 'string'
        ? data.response
        : typeof data.message === 'string'
          ? data.message
          : ''

  return {
    reply: reply || 'No response received from the chatbot.',
    message: typeof data.message === 'string' ? data.message : reply || '',
    action: typeof data.action === 'string' ? data.action : typeof data.intent === 'string' ? data.intent : 'UNRELATED',
    card_type: typeof data.card_type === 'string' ? data.card_type : typeof data.type === 'string' ? data.type : '',
    intent: typeof data.intent === 'string' ? data.intent : 'UNRELATED',
    confidence: Number.isFinite(Number(data.confidence)) ? Number(data.confidence) : 0,
    workflow: typeof data.workflow === 'string' ? data.workflow : '',
    route: typeof data.route === 'string' ? data.route : '',
    type: typeof data.card_type === 'string' ? data.card_type : typeof data.type === 'string' ? data.type : '',
    data: data && typeof data.data === 'object' ? data.data : null,
    suggestions: Array.isArray(data.suggestions) ? data.suggestions : [],
    status: typeof data.status === 'string' ? data.status : 'success',
  }
}
