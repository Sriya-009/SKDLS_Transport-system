import { requestJson } from './http'

export async function registerUser(payload) {
  return requestJson('/auth/register', {
    method: 'POST',
    body: payload,
  })
}

export async function loginUser(payload) {
  return requestJson('/auth/login', {
    method: 'POST',
    body: payload,
  })
}

export async function getProfile() {
  return requestJson('/auth/profile')
}

export async function logoutUser() {
  return requestJson('/auth/logout', {
    method: 'POST',
  })
}