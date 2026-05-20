/**
 * Calculate the distance between two cities using OpenStreetMap and OSRM APIs
 * @param {string} source - Source city name
 * @param {string} destination - Destination city name
 * @returns {Promise<number>} - Distance in km
 */
export const calculateDistance = async (source, destination) => {
  const getCoordinates = async (city) => {
    const response = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(city)}&format=json&limit=1`,
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
    const [sourceCoords, destinationCoords] = await Promise.all([
      getCoordinates(source),
      getCoordinates(destination),
    ])

    const response = await fetch(
      `https://router.project-osrm.org/route/v1/driving/${sourceCoords.longitude},${sourceCoords.latitude};${destinationCoords.longitude},${destinationCoords.latitude}?overview=false`,
    )

    if (!response.ok) {
      throw new Error('Distance not available')
    }

    const data = await response.json()
    const routeDistance = data.routes?.[0]?.distance

    if (typeof routeDistance !== 'number') {
      throw new Error('Distance not available')
    }

    // Adjust the distance by 8% to account for actual road conditions
    const adjusted = Math.round((routeDistance / 1000) * 1.08)
    return adjusted
  }

  return await getRouteDistance()
}

/**
 * Calculate the price based on distance
 * @param {number} distance - Distance in km
 * @returns {number} - Price in the local currency
 */
export const calculatePrice = (distance) => {
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

/**
 * Extract numeric distance from a message like "Estimated Lorry Distance: 275 km"
 * Returns the distance as a Number, or null if no valid number could be parsed.
 * @param {string} distanceMessage
 * @returns {number|null}
 */
export const extractDistance = (distanceMessage) => {
  if (typeof distanceMessage !== 'string') return null

  // Match integers or decimals followed by optional space and 'km' (case-insensitive)
  const re = /([0-9]+(?:[\.,][0-9]+)?)\s*km/i
  const m = distanceMessage.match(re)
  if (!m) return null

  // Normalize decimal comma to dot
  const numStr = m[1].replace(',', '.')
  const n = Number(numStr)
  return Number.isFinite(n) ? n : null
}
