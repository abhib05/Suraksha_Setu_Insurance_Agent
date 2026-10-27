// Family intake (M4): chat with the conversation agent, consent, photo uploads with what we
// read from each, then "finish" so the case workflow checks covers and briefs the family.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, BASE, type Schemas } from '../api/client'

type Msg = { role: string; content: string }
type Doc = Schemas['DocumentView']
type CaseState = {
  stage: string
  briefing: string | null
  preselected: string[]
  entitlements: { entitlement_id: string; scheme_id: string; expected_payout_inr: number | null }[]
  rule_results: { entitlement_id: string; status: string; explanation_en: string; rule_ids: string[] }[]
  claims: Record<string, string>
}

const CASE_KEY = 'suraksha.caseId'

function readCaseId(): string | null {
  try {
    return localStorage.getItem(CASE_KEY)
  } catch {
    return null
  }
}

function saveCaseId(id: string | null) {
  try {
    if (id) localStorage.setItem(CASE_KEY, id)
    else localStorage.removeItem(CASE_KEY)
  } catch {
    /* private mode: the case just won't survive a reload */
  }
}

export default function Intake() {
  const [caseId, setCaseId] = useState<string | null>(readCaseId)
  const [messages, setMessages] = useState<Msg[]>([])
  const [consent, setConsent] = useState(false)
  const [complete, setComplete] = useState(false)
  const [finished, setFinished] = useState(false)
  const [docs, setDocs] = useState<Doc[]>([])
  const [state, setState] = useState<CaseState | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string[] | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async (id: string) => {
    const [chat, documents] = await Promise.all([
      api<Schemas['ChatHistoryOut']>('GET', `/cases/${id}/chat`),
      api<Doc[]>('GET', `/cases/${id}/documents`),
    ])
    setMessages(chat.messages as Msg[])
    setConsent(chat.consent_active)
    setComplete(chat.intake_complete)
    setFinished(chat.finished)
    setDocs(documents)
    try {
      setState(await api<CaseState>('GET', `/cases/${id}/workflow`))
    } catch {
      setState(null)
    }
  }, [])

  useEffect(() => {
    if (caseId) refresh(caseId).catch((e) => setError(String(e)))
  }, [caseId, refresh])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' }) // newer browsers return a Promise here
  }, [messages])

  // While the workflow is working, poll its state.
  useEffect(() => {
    if (!caseId || !finished || state?.stage === 'awaiting_choice' || state?.stage === 'closed') return
    const t = setInterval(() => refresh(caseId).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [caseId, finished, state?.stage, refresh])

  async function run<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function startCase() {
    await run('Starting', async () => {
      const c = await api<Schemas['CaseOut']>('POST', '/cases', { channel: 'web_chat' })
      try {
        await api('POST', `/cases/${c.case_id}/workflow`, { wait_for_intake: true })
      } catch (e) {
        setError(`Case opened, but the workflow did not start (${e}). Is the worker running?`)
      }
      saveCaseId(c.case_id)
      setCaseId(c.case_id)
      await send('Hello', c.case_id)
    })
  }

  async function send(text: string, id = caseId) {
    if (!id || !text.trim()) return
    setMessages((m) => [...m, { role: 'user', content: text }])
    setDraft('')
    const out = await run('Thinking', () =>
      api<Schemas['ChatOut']>('POST', `/cases/${id}/chat`, { message: text }),
    )
    if (out) {
      setMessages((m) => [...m, { role: 'assistant', content: out.reply }])
      setConsent(out.consent_active)
      setComplete(out.intake_complete)
    }
  }

  async function agree() {
    if (!caseId) return
    const name = window.prompt('Your name (kept encrypted, used only for this claim):')
    if (!name) return
    await run('Recording consent', () =>
      api('POST', `/cases/${caseId}/consent`, { given_by_name: name, method: 'chat' }),
    )
    setConsent(true)
    await send('I agree.')
  }

  async function upload(files: FileList | null) {
    if (!caseId || !files) return
    for (const file of Array.from(files)) {
      const form = new FormData()
      form.append('file', file)
      await run(`Reading ${file.name}`, () => api('POST', `/cases/${caseId}/documents`, form))
    }
    await refresh(caseId)
  }

  async function finish() {
    if (!caseId) return
    await run('Finishing', () => api('POST', `/cases/${caseId}/intake/finish`))
    setFinished(true)
    await refresh(caseId)
  }

  async function confirmCovers() {
    if (!caseId || !state) return
    await run('Starting claims', () =>
      api('POST', `/cases/${caseId}/workflow/choose-covers`, {
        entitlement_ids: chosen ?? state.preselected,
      }),
    )
    await refresh(caseId)
  }

  function newCase() {
    saveCaseId(null)
    setCaseId(null)
    setMessages([])
    setDocs([])
    setState(null)
    setConsent(false)
    setComplete(false)
    setFinished(false)
    setChosen(null)
  }

  if (!caseId) {
    return (
      <section className="intake-start">
        <h1>Help after a death in the family</h1>
        <p className="muted">
          Free. Nobody from Suraksha Setu will ever ask you for money, OTP or PIN. You can stop at
          any time.
        </p>
        <button className="primary" onClick={startCase} disabled={!!busy}>
          {busy ?? 'Start'}
        </button>
        {error && <p className="error">{error}</p>}
      </section>
    )
  }

  const results = new Map(state?.rule_results.map((r) => [r.entitlement_id, r]) ?? [])
  const picked = chosen ?? state?.preselected ?? []

  return (
    <section className="intake">
      <div className="intake-head">
        <h1>Intake</h1>
        <span className="muted mono">{caseId}</span>
        <span className="spacer" />
        {state && <span className="pill">{state.stage.replace(/_/g, ' ')}</span>}
        <button onClick={newCase}>New case</button>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="intake-grid">
        <div className="panel chat">
          <div className="messages">
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.content}
              </div>
            ))}
            {busy === 'Thinking' && <div className="msg assistant muted">…</div>}
            <div ref={bottom} />
          </div>
          {!consent && (
            <div className="consent">
              <span>We use your details only to find covers and prepare claims for a reviewer.</span>
              <button className="primary" onClick={agree} disabled={!!busy}>
                I agree
              </button>
            </div>
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault()
              send(draft)
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Type your answer"
              disabled={!!busy || finished}
            />
            <button type="submit" disabled={!!busy || !draft.trim() || finished}>
              Send
            </button>
          </form>
        </div>

        <div className="panel docs">
          <div className="docs-head">
            <h2>Documents</h2>
            <label className={`button ${!consent || finished ? 'disabled' : ''}`}>
              Upload photos
              <input
                type="file"
                accept="image/*,application/pdf"
                multiple
                hidden
                disabled={!consent || finished}
                onChange={(e) => upload(e.target.files)}
              />
            </label>
          </div>
          {busy?.startsWith('Reading') && <p className="muted">{busy}…</p>}
          {docs.length === 0 && (
            <p className="muted">
              Passbook pages with recent entries, death certificate, FIR and post-mortem report
              (for an accident), and an ID proof.
            </p>
          )}
          {docs.map((d) => (
            <DocumentCard key={d.doc_id} caseId={caseId} doc={d} />
          ))}
          <button
            className="primary wide"
            onClick={finish}
            disabled={!consent || finished || !!busy}
            title={complete ? '' : 'The assistant has not confirmed every answer yet'}
          >
            {finished ? 'Sent for checking' : complete ? 'Finish and check covers' : 'Finish anyway'}
          </button>
        </div>
      </div>

      {state?.briefing && (
        <div className="panel briefing">
          <h2>What may apply</h2>
          {state.entitlements.length === 0 && <p>{state.briefing}</p>}
          {state.entitlements.map((e) => {
            const r = results.get(e.entitlement_id)
            const disabled = r?.status === 'not_eligible' || state.stage !== 'awaiting_choice'
            return (
              <label key={e.entitlement_id} className={`cover ${r?.status}`}>
                <input
                  type="checkbox"
                  disabled={disabled}
                  checked={picked.includes(e.entitlement_id)}
                  onChange={(ev) =>
                    setChosen(
                      ev.target.checked
                        ? [...picked, e.entitlement_id]
                        : picked.filter((x) => x !== e.entitlement_id),
                    )
                  }
                />
                <div>
                  <strong>{e.scheme_id.toUpperCase()}</strong>{' '}
                  <span className="pill">{r?.status.replace(/_/g, ' ')}</span>
                  {e.expected_payout_inr ? (
                    <span className="muted"> · up to ₹{e.expected_payout_inr.toLocaleString('en-IN')}</span>
                  ) : null}
                  <p>{r?.explanation_en}</p>
                  <p className="muted small">Rules: {r?.rule_ids.join(', ')}</p>
                </div>
              </label>
            )
          })}
          {state.stage === 'awaiting_choice' && state.entitlements.length > 0 && (
            <button className="primary" onClick={confirmCovers} disabled={!!busy}>
              Start these claims
            </button>
          )}
          {Object.keys(state.claims).length > 0 && (
            <p className="muted">Claims started. A reviewer will check each one before filing.</p>
          )}
        </div>
      )}
    </section>
  )
}

function DocumentCard({ caseId, doc }: { caseId: string; doc: Doc }) {
  const ext = doc.extraction as
    | { fields: { name: string; value: string | null; confidence: number }[]; tamper_signals: string[] }
    | null
  const canPreview = doc.masking_status === 'masked' || doc.masking_status === 'none_found'
  return (
    <div className="doc">
      {canPreview ? (
        <img src={`${BASE}/cases/${caseId}/documents/${doc.doc_id}/image`} alt={doc.filename} />
      ) : (
        <div className="noimg muted">{doc.masking_status}</div>
      )}
      <div className="doc-body">
        <div>
          <strong>{doc.doc_type?.replace(/_/g, ' ') ?? 'reading…'}</strong>
          {doc.aadhaar_masked > 0 && <span className="pill">Aadhaar masked</span>}
        </div>
        {!ext && <p className="muted small">Not read yet (the model is unavailable); a reviewer will check it.</p>}
        {ext && (
          <table>
            <tbody>
              {ext.fields.map((f, i) => (
                <tr key={i} className={f.confidence < 0.7 ? 'low' : ''}>
                  <td className="muted">{f.name}</td>
                  <td className="mono">{f.value}</td>
                  <td className="muted small">{Math.round(f.confidence * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {ext?.tamper_signals.map((s, i) => (
          <p key={i} className="warn small">
            ⚠ {s}
          </p>
        ))}
      </div>
    </div>
  )
}
