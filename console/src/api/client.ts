// Typed fetch helpers over the generated OpenAPI schema (regenerate: `uv run poe openapi && npm run gen:api`).
import type { paths } from './schema'

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

type GetPath = { [P in keyof paths]: paths[P] extends { get: unknown } ? P : never }[keyof paths]
type JsonOk<Op> = Op extends { responses: { 200: { content: { 'application/json': infer R } } } }
  ? R
  : never

export async function apiGet<P extends GetPath>(path: P): Promise<JsonOk<paths[P]['get']>> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}
