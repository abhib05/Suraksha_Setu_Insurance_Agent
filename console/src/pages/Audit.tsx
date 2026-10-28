// Audit log (M7): every step of one case in order, who or what did it (agent, model and
// prompt version, rule versions), and a check that the hash chain is unbroken.
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type Schemas } from '../api/client'
import { usePoll } from '../review'

type Event = Schemas['AuditEventOut']
type Chain = Schemas['ChainOut']

const ACTOR_KIND = (actor: string) => actor.split(':')[0]

function summary(e: Event): string {
  const d = e.detail ?? {}
  const parts = Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
    .map(([k, v]) => `${k}=${v}`)
  return parts.join(' · ')
}

export default function Audit() {
  const { caseId = '' } = useParams()
  const load = useCallback(() => api<Event[]>('GET', `/cases/${caseId}/audit`), [caseId])
  const { data, error } = usePoll(load, 8000)
  const [chain, setChain] = useState<Chain | null>(null)
  const [filter, setFilter] = useState('')

  const events = (data ?? []).filter(
    (e) => !filter || `${e.actor} ${e.action}`.toLowerCase().includes(filter.toLowerCase()),
  )

  return (
    <section>
      <p className="small"><Link to="/review">← Review queue</Link></p>
      <div className="intake-head">
        <h1>Audit log</h1>
        <span className="small muted mono">{caseId}</span>
        <span className="spacer" />
        <input placeholder="Filter (e.g. agent, gate, vault)" value={filter}
          onChange={(e) => setFilter(e.target.value)} />
        <button onClick={async () => setChain(await api<Chain>('GET', '/audit/verify'))}>
          Verify hash chain
        </button>
      </div>
      {chain && (
        <p className={chain.ok ? 'ok' : 'error'}>
          {chain.ok
            ? `Chain intact: all ${chain.events} events recompute to their stored hashes.`
            : `Chain broken at event ${chain.first_bad_id}.`}
        </p>
      )}
      <p className="small muted">
        Append-only: the database refuses updates and deletes, and each event's hash covers the
        one before it. Inputs and outputs are stored as hashes, never as names or numbers.
      </p>
      {error && <p className="error">{error}</p>}
      <table className="queue audit">
        <thead>
          <tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Detail</th><th>Hash</th></tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="mono small">{e.id}</td>
              <td className="small">{new Date(e.ts).toLocaleTimeString()}</td>
              <td><span className={`pill actor-${ACTOR_KIND(e.actor)}`}>{e.actor}</span></td>
              <td>
                <strong className="small">{e.action}</strong>
                {(e.model || e.prompt_version) && (
                  <div className="small muted">{e.model} {e.prompt_version && `prompt v${e.prompt_version}`}</div>
                )}
                {e.rule_versions && e.rule_versions.length > 0 && (
                  <div className="small muted">{e.rule_versions.join(', ')}</div>
                )}
              </td>
              <td className="small">{summary(e)}</td>
              <td className="mono small muted" title={e.hash}>{e.hash.slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && events.length === 0 && <p className="muted">No events.</p>}
    </section>
  )
}
