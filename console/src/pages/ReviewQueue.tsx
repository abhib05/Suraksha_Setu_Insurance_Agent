// Review queue (M6): every claim with where it stands (the ones waiting on a reviewer first),
// and the open reviewer tasks the workflows have raised.
import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Schemas } from '../api/client'
import { readReviewer, saveReviewer, SCHEME_NAMES, STAGE_LABELS, usePoll } from '../review'

type Row = Schemas['ClaimRow']
type Task = Schemas['TaskOut']

const needsReviewer = (r: Row) =>
  r.status === 'awaiting_approval' || r.status === 'awaiting_payout_confirmation' || r.open_tasks > 0

export default function ReviewQueue() {
  const [reviewer, setReviewer] = useState(readReviewer)
  const loadClaims = useCallback(() => api<Row[]>('GET', '/review/claims'), [])
  const loadTasks = useCallback(() => api<Task[]>('GET', '/tasks?assignee=reviewer'), [])
  const claims = usePoll(loadClaims)
  const tasks = usePoll(loadTasks)

  const waiting = claims.data?.filter(needsReviewer) ?? []
  const others = claims.data?.filter((r) => !needsReviewer(r)) ?? []

  return (
    <section>
      <div className="intake-head">
        <h1>Review queue</h1>
        <span className="spacer" />
        <label className="small">
          Reviewer{' '}
          <input
            value={reviewer}
            placeholder="your name"
            onChange={(e) => {
              setReviewer(e.target.value)
              saveReviewer(e.target.value)
            }}
          />
        </label>
      </div>
      {claims.error && <p className="error">Could not load claims: {claims.error}</p>}

      <h2>Waiting on you ({waiting.length})</h2>
      {claims.data && waiting.length === 0 && <p className="muted">Nothing waiting.</p>}
      {waiting.length > 0 && <ClaimTable rows={waiting} />}

      <TaskList tasks={tasks.data ?? []} reviewer={reviewer} onDone={tasks.reload} />

      <h2>All other claims ({others.length})</h2>
      {others.length > 0 ? <ClaimTable rows={others} /> : <p className="muted">None yet.</p>}
    </section>
  )
}

function ClaimTable({ rows }: { rows: Row[] }) {
  return (
    <table className="queue">
      <thead>
        <tr>
          <th>Claim</th>
          <th>Cover</th>
          <th>Stage</th>
          <th>Packet</th>
          <th>Verifier</th>
          <th>Claim number</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.claim_id}>
            <td>
              <Link to={`/review/${r.claim_id}`} className="mono">{r.claim_id}</Link>
              <div className="small muted mono">{r.case_id}</div>
            </td>
            <td>{SCHEME_NAMES[r.scheme_id] ?? r.scheme_id}</td>
            <td>
              <span className={`pill stage-${r.status}`}>{STAGE_LABELS[r.status] ?? r.status}</span>
              {r.open_tasks > 0 && <div className="small warn">{r.open_tasks} open task(s)</div>}
            </td>
            <td>{r.packet_version ? `v${r.packet_version}` : '—'}</td>
            <td><VerdictBadge v={r.verdict} /></td>
            <td className="mono">{r.claim_number ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function VerdictBadge({ v }: { v: Schemas['VerdictCounts'] | null | undefined }) {
  if (!v) return <span className="muted">—</span>
  if (!v.passed) return <span className="error">{v.blockers} blocker(s)</span>
  return <span>passed{v.warnings > 0 && <span className="warn"> · {v.warnings} warning(s)</span>}</span>
}

const TASK_LABELS: Record<string, string> = {
  verifier_failed: 'Verifier blocked a packet',
  portal_rejected: 'Claim desk rejected a filing',
  step_failed: 'A step kept failing',
  eligibility_review: 'Check weak evidence',
  document_review: 'Check a document',
  appeal: 'Short payment: appeal',
  family_support: 'Call the family back',
}

export function TaskList({ tasks, reviewer, onDone, title = 'Open reviewer tasks' }: {
  tasks: Task[]
  reviewer: string
  onDone: () => void
  title?: string
}) {
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const open = tasks.filter((t) => t.status === 'open' && t.assignee === 'reviewer')

  async function resolve(t: Task) {
    setError(null)
    try {
      await api('POST', `/tasks/${t.id}/resolve`, { resolved_by: reviewer, note: notes[t.id] || null })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <>
      <h2>{title} ({open.length})</h2>
      {open.length === 0 && <p className="muted">No open tasks.</p>}
      {error && <p className="error">{error}</p>}
      {open.map((t) => (
        <div key={t.id} className="panel task">
          <div className="intake-head">
            <strong>{TASK_LABELS[t.kind] ?? t.kind}</strong>
            {t.claim_id && <Link to={`/review/${t.claim_id}`} className="small mono">{t.claim_id}</Link>}
            <span className="spacer" />
            <span className="small muted">{new Date(t.created_at).toLocaleString()}</span>
          </div>
          <p>{t.detail}</p>
          {t.kind === 'eligibility_review' ? (
            <EligibilityReview caseId={t.case_id} reviewer={reviewer} onDone={onDone} />
          ) : (
          <div className="composer">
            <input
              placeholder="What did you do? (kept in the audit log)"
              value={notes[t.id] ?? ''}
              onChange={(e) => setNotes({ ...notes, [t.id]: e.target.value })}
            />
            <button className="primary" disabled={!reviewer} onClick={() => resolve(t)}
              title={reviewer ? '' : 'Enter your name first'}>
              Mark done
            </button>
          </div>
          )}
        </div>
      ))}
    </>
  )
}

type CaseState = {
  stage: string
  needs_review: string[]
  entitlements: { entitlement_id: string; scheme_id: string; strength: string;
    evidence: { doc_id: string; field: string; snippet: string }[] }[]
}

/** The eligibility gate: tick the unsure covers the evidence supports; unticked ones are
 * dropped before the family is told anything. */
function EligibilityReview({ caseId, reviewer, onDone }: {
  caseId: string; reviewer: string; onDone: () => void
}) {
  const load = useCallback(() => api<CaseState>('GET', `/cases/${caseId}/workflow`), [caseId])
  const { data } = usePoll(load, 10000)
  const [keep, setKeep] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  if (!data) return <p className="muted small">Loading covers…</p>
  if (data.stage !== 'awaiting_eligibility_review') return <p className="muted small">Already reviewed.</p>
  const unsure = data.entitlements.filter((e) => data.needs_review.includes(e.entitlement_id))

  async function submit() {
    setError(null)
    try {
      await api('POST', `/cases/${caseId}/workflow/eligibility-review`, {
        approver: reviewer, approved_entitlement_ids: keep, comment: comment || null,
      })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div>
      {unsure.map((e) => (
        <label key={e.entitlement_id} className="cover">
          <input type="checkbox" checked={keep.includes(e.entitlement_id)}
            onChange={(ev) => setKeep(ev.target.checked ? [...keep, e.entitlement_id]
              : keep.filter((k) => k !== e.entitlement_id))} />
          <span>
            <strong>{SCHEME_NAMES[e.scheme_id] ?? e.scheme_id}</strong> · evidence {e.strength}
            {e.evidence.map((ev, i) => (
              <span key={i} className="small muted mono" style={{ display: 'block' }}>
                {ev.snippet} ({ev.doc_id}#{ev.field})
              </span>
            ))}
          </span>
        </label>
      ))}
      {error && <p className="error">{error}</p>}
      <div className="composer">
        <input placeholder="Why (optional, kept in the audit log)" value={comment}
          onChange={(e) => setComment(e.target.value)} />
        <button className="primary" disabled={!reviewer} onClick={submit}>
          Keep {keep.length} of {unsure.length}
        </button>
      </div>
    </div>
  )
}
