// Packet review (M6): the printable packet with each field's source and the verifier's
// findings beside it, the coordinator's summary, and the human gates for this claim:
// approve or return this exact packet version, then confirm the payout.
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, BASE, type Schemas } from '../api/client'
import { readReviewer, rupees, SCHEME_NAMES, STAGE_LABELS, usePoll } from '../review'
import { TaskList, VerdictBadge } from './ReviewQueue'

type Finding = { severity: string; target: string; reason: string }
type ClaimState = {
  stage: string
  packet_version: number
  verdict: { passed: boolean; findings: Finding[] } | null
  summary: string | null
  claim_number: string | null
  paid_amount_inr: number | null
}

export default function PacketReview() {
  const { claimId = '' } = useParams()
  const reviewer = readReviewer()
  const loadDetail = useCallback(() => api<Schemas['ClaimDetail']>('GET', `/review/claims/${claimId}`), [claimId])
  const loadState = useCallback(() => api<ClaimState>('GET', `/claims/${claimId}`), [claimId])
  const detail = usePoll(loadDetail)
  const state = usePoll(loadState)
  const [shown, setShown] = useState<number | null>(null)

  const d = detail.data
  const st = state.data
  const latest = d?.packet_version ?? null
  const version = shown ?? latest
  const reload = () => {
    detail.reload()
    state.reload()
  }

  if (detail.error && !d) return <section><p className="error">{detail.error}</p></section>
  if (!d) return <section><p className="muted">Loading…</p></section>

  return (
    <section>
      <p className="small"><Link to="/review">← Review queue</Link></p>
      <div className="intake-head">
        <h1>{SCHEME_NAMES[d.scheme_id] ?? d.scheme_id}</h1>
        <span className={`pill stage-${d.status}`}>{STAGE_LABELS[d.status] ?? d.status}</span>
        <span className="spacer" />
        <span className="small muted mono">{d.claim_id} · {d.case_id}</span>
      </div>
      <p className="small muted">
        Expected payout {rupees(d.expected_payout_inr)}
        {d.claim_number && <> · claim number <span className="mono">{d.claim_number}</span></>}
      </p>

      <div className="review-grid">
        <div>
          {d.versions.length > 0 ? (
            <>
              <div className="intake-head">
                <strong>Packet</strong>
                {d.versions.map((v) => (
                  <button key={v.version} className={v.version === version ? 'primary' : ''}
                    onClick={() => setShown(v.version)}>
                    v{v.version}
                  </button>
                ))}
                <span className="spacer" />
                <a className="small" href={`${BASE}/claims/${claimId}/packet.html?version=${version}`}
                  target="_blank" rel="noreferrer">Open printable ↗</a>
              </div>
              <iframe key={version} className="packet-frame" title={`Packet v${version}`}
                src={`${BASE}/claims/${claimId}/packet.html?version=${version}`} />
            </>
          ) : (
            <p className="muted">No packet built yet.</p>
          )}
        </div>

        <aside>
          {st && <Gate claimId={claimId} st={st} reviewer={reviewer} expected={d.expected_payout_inr}
            onDone={reload} />}
          {state.error && !st && <p className="muted small">Workflow state unavailable: {state.error}</p>}

          <div className="panel">
            <h3>Verifier (v{latest})</h3>
            <VerdictBadge v={d.versions.find((v) => v.version === latest)?.verdict} />
            {st?.verdict?.findings.length ? (
              <ul className="findings">
                {st.verdict.findings.map((f, i) => (
                  <li key={i} className={f.severity}>
                    <strong>{f.severity}</strong> <span className="mono small">{f.target}</span>
                    <div>{f.reason}</div>
                  </li>
                ))}
              </ul>
            ) : st?.verdict ? <p className="small muted">No findings.</p> : null}
          </div>

          {d.decisions.length > 0 && (
            <div className="panel">
              <h3>Decisions</h3>
              <ul className="findings">
                {d.decisions.map((x, i) => (
                  <li key={i}>
                    <strong>{x.decision}</strong> {x.gate.replace(/_/g, ' ')}
                    {x.packet_version && ` v${x.packet_version}`} by {x.approver}
                    <div className="small muted">{new Date(x.decided_at).toLocaleString()}</div>
                    {x.comment && <div>“{x.comment}”</div>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>

      <TaskList tasks={d.tasks} reviewer={reviewer} onDone={reload} title="Tasks on this claim" />
    </section>
  )
}

function Gate({ claimId, st, reviewer, expected, onDone }: {
  claimId: string; st: ClaimState; reviewer: string; expected: number | null; onDone: () => void
}) {
  const [comment, setComment] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function post(path: string, body: unknown) {
    setBusy(true)
    setError(null)
    try {
      await api('POST', path, body)
      setComment('')
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const noName = !reviewer && <p className="small warn">Enter your name on the review queue first.</p>

  if (st.stage === 'awaiting_approval') {
    const v = st.packet_version
    return (
      <div className="panel gate">
        <h3>Approve packet v{v}?</h3>
        {st.summary && <p>{st.summary}</p>}
        <p className="small muted">
          Approving files exactly this version with the claim desk. Returning rebuilds it with
          your comment.
        </p>
        <textarea rows={3} placeholder="Comment (needed to return)" value={comment}
          onChange={(e) => setComment(e.target.value)} />
        {noName}
        {error && <p className="error">{error}</p>}
        <div className="composer">
          <button className="primary" disabled={busy || !reviewer}
            onClick={() => post(`/claims/${claimId}/approve`, { approver: reviewer, packet_version: v, comment: comment || null })}>
            Approve and file v{v}
          </button>
          <button disabled={busy || !reviewer || !comment.trim()}
            onClick={() => post(`/claims/${claimId}/return`, { approver: reviewer, packet_version: v, comment })}>
            Return for changes
          </button>
        </div>
      </div>
    )
  }

  if (st.stage === 'awaiting_payout_confirmation') {
    const reported = st.paid_amount_inr
    return (
      <div className="panel gate">
        <h3>Confirm the payout</h3>
        <p>
          The claim desk reports {rupees(reported)} paid (expected {rupees(expected)}). Confirm the
          amount that reached the nominee's account. Less than expected opens an appeal task.
        </p>
        <div className="composer">
          <input type="number" min={0} placeholder={String(reported ?? expected ?? '')} value={amount}
            onChange={(e) => setAmount(e.target.value)} />
          <button className="primary" disabled={busy || !reviewer}
            onClick={() => post(`/claims/${claimId}/confirm-payout`, {
              approver: reviewer, amount_inr: Number(amount || reported || expected || 0),
            })}>
            Confirm
          </button>
        </div>
        {noName}
        {error && <p className="error">{error}</p>}
      </div>
    )
  }

  return (
    <div className="panel">
      <h3>{STAGE_LABELS[st.stage] ?? st.stage}</h3>
      {st.summary && <p className="small">{st.summary}</p>}
      {st.claim_number && <p className="small">Claim number <span className="mono">{st.claim_number}</span></p>}
      {st.paid_amount_inr != null && <p className="small">Paid {rupees(st.paid_amount_inr)}</p>}
    </div>
  )
}
