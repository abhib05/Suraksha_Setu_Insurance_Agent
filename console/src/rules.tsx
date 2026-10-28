// Rule chips: every cited rule id opens the rule itself (what it checks, its version, the
// source it comes from), so a reviewer or family member never has to take a citation on trust.
import { useEffect, useState } from 'react'
import { api, type Schemas } from './api/client'

type Rule = Schemas['RuleOut']

let cache: Promise<Map<string, Rule>> | null = null

function loadRules(): Promise<Map<string, Rule>> {
  cache ??= api<Rule[]>('GET', '/rules')
    .then((rs) => new Map(rs.map((r) => [r.id, r])))
    .catch((e) => {
      cache = null
      throw e
    })
  return cache
}

export function RuleChips({ ids, passed }: { ids: string[]; passed?: boolean }) {
  const [rules, setRules] = useState<Map<string, Rule> | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => {
    loadRules().then(setRules, () => setRules(new Map()))
  }, [])
  const rule = open ? rules?.get(open) : undefined
  return (
    <div className="rules">
      <span className="muted small">Rules: </span>
      {ids.map((id) => (
        <button key={id} className={`chip ${open === id ? 'on' : ''}`} title={rules?.get(id)?.title}
          onClick={(e) => {
            e.preventDefault()
            setOpen(open === id ? null : id)
          }}>
          {id}
        </button>
      ))}
      {rule && (
        <div className="rule-card">
          <strong>{rule.id}: {rule.title}</strong>
          <p className="small">
            {passed === false ? rule.explain_fail : passed ? rule.explain_pass : (
              <>Passes: {rule.explain_pass}<br />Fails: {rule.explain_fail}</>
            )}
          </p>
          <p className="small muted">
            Version {rule.version}, in force from {rule.effective_from}
            {rule.effective_to ? ` to ${rule.effective_to}` : ''} · checks {rule.requires.join(', ')}
          </p>
          <p className="small">
            Source:{' '}
            {rule.source_url ? (
              <a href={rule.source_url} target="_blank" rel="noreferrer">{rule.source_title ?? rule.source_id}</a>
            ) : rule.source_id}
            {rule.source_url && <span className="muted"> ({rule.source_id})</span>}
          </p>
          <details>
            <summary className="small muted">Rule logic (JSON-logic)</summary>
            <pre className="mono small">{JSON.stringify(rule.logic, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
