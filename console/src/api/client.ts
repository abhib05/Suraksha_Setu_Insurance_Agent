// Typed fetch helpers over the generated OpenAPI schema (regenerate: `uv run poe openapi && npm run gen:api`).
import type { components, paths } from './schema'

export const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type Schemas = components['schemas']

type GetPath = { [P in keyof paths]: paths[P] extends { get: unknown } ? P : never }[keyof paths]
type JsonOk<Op> = Op extends { responses: { 200: { content: { 'application/json': infer R } } } }
  ? R
  : never

export async function apiGet<P extends GetPath>(path: P): Promise<JsonOk<paths[P]['get']>> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.status = status
    this.detail = detail
  }
}

/** For routes with path parameters: callers name the response type from `Schemas`. */
export async function api<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const res = await fetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json()
}
