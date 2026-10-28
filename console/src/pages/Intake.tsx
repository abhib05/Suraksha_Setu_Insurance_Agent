// Family intake (M4): chat with the conversation agent, consent, photo uploads with what we
// read from each, then "finish" so the case workflow checks covers and briefs the family.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, BASE, type Schemas } from '../api/client'
import { RuleChips } from '../rules'
import { startRecording, type Recorder } from '../voice'

type Msg = { role: string; content: string }
type Doc = Schemas['DocumentView']
type CaseState = {
  stage: string
  briefing: string | null
  preselected: string[]
  entitlements: {
    entitlement_id: string
    scheme_id: string
    strength: string
    expected_payout_inr: number | null
    evidence: { doc_id: string; field: string; snippet: string }[]
  }[]
  rule_results: { entitlement_id: string; status: string; explanation_en: string; rule_ids: string[] }[]
  claims: Record<string, string>
}

const CASE_KEY = 'suraksha.caseId'

// "2026-05-28|PMJJBY PREM RENEWAL|436|DR" -> "2026-05-28 · PMJJBY PREM RENEWAL · ₹436 debit"
function txnText(snippet: string): string {
  const [on, desc, amount, side] = snippet.split('|')
  if (!side) return snippet
  return `${on} · ${desc} · ₹${Number(amount).toLocaleString('en-IN')} ${side === 'DR' ? 'debit' : 'credit'}`
}

function readCaseId(): string | null {
  // `/?case=<id>` opens a given case (e.g. one prepared by scripts/demo_case.py).
  const fromUrl = new URLSearchParams(window.location.search).get('case')
  if (fromUrl) {
    saveCaseId(fromUrl)
    window.history.replaceState(null, '', window.location.pathname)
    return fromUrl
  }
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
  const [consentName, setConsentName] = useState('')
  const [complete, setComplete] = useState(false)
  const [finished, setFinished] = useState(false)
  const [docs, setDocs] = useState<Doc[]>([])
  const [state, setState] = useState<CaseState | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [reading, setReading] = useState(0)
  const [recorder, setRecorder] = useState<Recorder | null>(null)
  const [voiceLang, setVoiceLang] = useState('auto')
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
    const name = consentName.trim()
    if (!caseId || !name) return
    await run('Recording consent', () =>
      api('POST', `/cases/${caseId}/consent`, { given_by_name: name, method: 'chat' }),
    )
    setConsent(true)
    await send('I agree.')
  }

  async function upload(files: FileList | null) {
    if (!caseId || !files) return
    // All photos are read at once, and the chat stays open meanwhile: reading takes a while,
    // and the family can keep answering questions. Each photo shows up as soon as it is read.
    const list = Array.from(files)
    setReading((n) => n + list.length)
    setError(null)
    const results = await Promise.allSettled(list.map(async (file) => {
      const form = new FormData()
      form.append('file', file)
      try {
        await api('POST', `/cases/${caseId}/documents`, form)
      } finally {
        setReading((n) => n - 1)
      }
      setDocs(await api<Doc[]>('GET', `/cases/${caseId}/documents`))
    }))
    const failed = results.flatMap((r, i) => (r.status === 'rejected' ? [list[i].name] : []))
    if (failed.length) setError(`Could not read ${failed.join(', ')}`)
  }

  // Push to talk: first press records, second press stops and puts the words in the box,
  // where the family can read and fix them before sending.
  async function voice() {
    if (!caseId) return
    if (!recorder) {
      setError(null)
      try {
        setRecorder(await startRecording())
      } catch {
        setError('Could not use the microphone. Please allow it, or type instead.')
      }
      return
    }
    const r = recorder
    setRecorder(null)
    await run('Listening', async () => {
      const form = new FormData()
      form.append('audio', await r.stop(), 'voice.wav')
      if (voiceLang !== 'auto') form.append('language', voiceLang)
      const out = await api<Schemas['VoiceOut']>('POST', `/cases/${caseId}/voice`, form)
      if (!out.text) throw new Error('I could not hear any words. Please try again, or type.')
      setDraft((d) => (d.trim() ? `${d.trim()} ${out.text}` : out.text))
    })
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
        <Link to={`/audit/${caseId}`} className="small">Audit log</Link>
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
              <input placeholder="Your name (kept encrypted)" value={consentName}
                onChange={(e) => setConsentName(e.target.value)} />
              <button className="primary" onClick={agree} disabled={!!busy || !consentName.trim()}>
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
            {consent && !finished && (
              <>
                <select value={voiceLang} onChange={(e) => setVoiceLang(e.target.value)}
                  title="Language you will speak" disabled={!!recorder}>
                  <option value="auto">Auto</option>
                  <option value="hindi">हिन्दी</option>
                  <option value="kannada">ಕನ್ನಡ</option>
                  <option value="english">English</option>
                </select>
                <button type="button" className={recorder ? 'recording' : ''} onClick={voice}
                  disabled={!!busy} title={recorder ? 'Stop and turn into text' : 'Speak your answer'}>
                  {recorder ? '■ Stop' : busy === 'Listening' ? '…' : '🎤 Speak'}
                </button>
              </>
            )}
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
          {reading > 0 && <p className="muted">Reading {reading} photo{reading > 1 ? 's' : ''}…</p>}
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
            disabled={!consent || finished || !!busy || reading > 0}
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
                  {e.expected_payout_inr && r?.status !== 'not_eligible' ? (
                    <span className="muted"> · up to ₹{e.expected_payout_inr.toLocaleString('en-IN')}</span>
                  ) : null}
                  <p>{r?.explanation_en}</p>
                  {e.evidence.length > 0 && (
                    <ul className="evidence small">
                      {e.evidence.map((ev, i) => (
                        <li key={i}>
                          <span className="mono">{txnText(ev.snippet)}</span>{' '}
                          <span className="muted">
                            ({(docs.find((d) => d.doc_id === ev.doc_id)?.doc_type ?? ev.doc_id).replace(/_/g, ' ')}
                            {e.strength === 'possible' ? ', weak' : ''})
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {r && <RuleChips ids={r.rule_ids} passed={r.status === 'eligible' ? true : r.status === 'not_eligible' ? false : undefined} />}
                </div>
              </label>
            )
          })}
          {state.stage === 'awaiting_choice' && state.entitlements.length > 0 && (
            <button className="primary" onClick={confirmCovers} disabled={!!busy}>
              {picked.length === 0
                ? 'Nothing to claim: close the case'
                : `Start ${picked.length === 1 ? 'this claim' : `these ${picked.length} claims`}`}
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
