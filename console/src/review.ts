// Shared bits for the reviewer pages: the reviewer's name (kept per browser) and polling.
import { useCallback, useEffect, useState } from 'react'

const NAME_KEY = 'suraksha.reviewer'

export function readReviewer(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? ''
  } catch {
    return ''
  }
}

export function saveReviewer(name: string) {
  try {
    localStorage.setItem(NAME_KEY, name)
  } catch {
    /* private mode: asked again next time */
  }
}

/** Load now and every `ms` while the page is open; `reload` forces a fetch after an action. */
export function usePoll<T>(load: () => Promise<T>, ms = 4000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(async () => {
    try {
      setData(await load())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [load])
  useEffect(() => {
    reload()
    const t = setInterval(reload, ms)
    return () => clearInterval(t)
  }, [reload, ms])
  return { data, error, reload }
}

export const SCHEME_NAMES: Record<string, string> = {
  pmjjby: 'PMJJBY (life cover)',
  pmsby: 'PMSBY (accident cover)',
  rupay: 'RuPay card accident cover',
}

export const STAGE_LABELS: Record<string, string> = {
  cover_found: 'Cover found',
  collecting_facts: 'Waiting on family: facts',
  collecting_documents: 'Waiting on family: documents',
  building_packet: 'Building packet',
  awaiting_approval: 'Needs your approval',
  submitted: 'Filed',
  under_review: 'With the claim desk',
  awaiting_payout_confirmation: 'Confirm payout',
  appeal_open: 'Short-paid: appeal open',
  closed_paid: 'Paid',
  closed_short_paid: 'Short-paid (appeal)',
  closed_not_eligible: 'Not eligible',
}

export const rupees = (n: number | null | undefined) =>
  n == null ? '—' : `₹${n.toLocaleString('en-IN')}`
