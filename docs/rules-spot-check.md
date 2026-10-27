# Rules spot-check (M3)

Checker: Abhinav · Status: **not yet checked** · Rules version: PMJJBY/PMSBY `2025-26.1`, RuPay `2026-27.1`

For each row, open the source, confirm the "We coded" column matches it, and tick ✅ or write
what is wrong. The **⚠ questions** at the bottom are the places where the source was unclear
or came from a single bank's document. They matter most.

Rule files: [`rules/`](../rules) · Scheme facts: [`registry/`](../registry) · Tests: [`tests/test_m3_rules.py`](../tests/test_m3_rules.py)

## Sources used

| ID | Document | Link |
|---|---|---|
| PMJJBY-RULES-2022 | Revised Rules for PMJJBY (w.e.f. 1.6.2022), hosted by J&K Bank | [PDF](https://jkb.bank.in/pdfs/fid/PMJJBY/PMJJBY%20Policy%20Details.pdf) |
| PMSBY-SCC-2025 | PMSBY detail of plan, bank summary (Sept 2025) | [PDF](https://scc.bank.in/resources/assets/upload/file/910_20250923095851.pdf) |
| RUPAY-NPCI-2627 | NPCI Annexure A, Claims Process, RuPay Insurance Program 2026-27 (PMJDY cards), hosted by J&K Bank | [PDF](https://jkbank.com/sites/default/files/Claim%20Process%20under%20PMJDY%20Rupay%20card%20Insurance%20Program%20%20for%20FY%202026-27.pdf) |
| RUPAY-IDFC | IDFC FIRST Bank PMJDY card insurance scheme | [PDF](https://www.idfcfirstbank.com/content/dam/IDFCFirstBank/PDF/IDFC-FIRST-BANK-RUPAY-CLASSIC-CARD-INSURANCE-new.pdf) |

## Eligibility rules

| Rule | We coded | Source | ✅ / correction |
|---|---|---|---|
| PMJJBY-R1 | Premium (₹436, or pro-rata ₹342/228/114) was debited on or before the death, and the cover year it bought (1 June – 31 May) includes the death date. A full premium debited in May renews the year starting that June. | PMJJBY-RULES-2022 §3, 4, 6, 9(2) | |
| PMJJBY-R2 | Non-accidental death within the first 30 days after enrolment (or re-joining) is not covered. Accidental death in that window is covered. Day 30 onward is covered. | PMJJBY-RULES-2022 §4 | |
| PMJJBY-R3 | Covered up to age 55 at death (inclusive). | PMJJBY-RULES-2022 §9(1) | |
| PMSBY-R1 | Premium ₹20 debited on/before the accident; the cover year it bought includes the accident date. | PMSBY-SCC-2025 | |
| PMSBY-R2 | Only accidental death is covered; illness, natural death and suicide are not. | PMSBY-SCC-2025 | |
| PMSBY-R3 | Covered up to age 70 at death (inclusive). | PMSBY-SCC-2025 | |
| PMSBY-R4 | Accident within the first 30 days after joining is not covered. | PMSBY-SCC-2025 | |
| RUPAY-R1 | Only accidental death is covered. | RUPAY-IDFC | |
| RUPAY-R2 | At least one card transaction 0–90 days before the accident (accident day counts), **or** the card was issued 0–90 days before the accident. | RUPAY-NPCI-2627, RUPAY-IDFC | |

## Payouts and documents

| Item | We coded | Source | ✅ / correction |
|---|---|---|---|
| PMJJBY payout | ₹2,00,000 | PMJJBY-RULES-2022 §5 | |
| PMSBY payout | ₹2,00,000 accidental death | PMSBY-SCC-2025 | |
| RuPay payout | ₹1,00,000 if card issued on/before 28 Aug 2018; ₹2,00,000 after | RUPAY-IDFC | |
| PMJJBY documents | death certificate, passbook, ID proof (+ FIR and post-mortem if accidental death in lien period) + claim form + discharge receipt | PMJJBY-RULES-2022 | |
| PMSBY documents | death certificate, FIR, post-mortem, passbook, ID proof + claim form (+ driving licence/RC if they were driving) | PMSBY-SCC-2025 | |
| RuPay documents | death certificate, FIR, post-mortem, ID proof (Aadhaar of cardholder and nominee), passbook + claim form + bank declaration | RUPAY-NPCI-2627 §H | |
| Deadlines | RuPay: intimate within 90 days of accident, documents within 60 days of intimation. PMSBY: report within 30 days of accident. | RUPAY-NPCI-2627 §A–B, PMSBY-SCC-2025 | |

## How the passbook is read (matchers)

| Behaviour | ✅ / correction |
|---|---|
| A PMJJBY debit is a debit of 436 / 342 / 228 / 114 / 330 (pre-2022) whose line mentions PMJJBY, JJBY, PMJJ or JEEVAN JYOTI → **strong**. Same amount with only generic words (INS, PREM, LIC, BIMA, POLICY) → **possible** (reviewer confirms). | |
| A PMSBY debit is 20 / 12 (pre-2022) with PMSBY, SBY, PMSB or SURAKSHA BIMA → strong; generic insurance words → possible. | |
| A premium debited in any month other than May or June is treated as a new enrolment or reinstatement, so a fresh 30-day lien starts that day. | |
| If only one May debit is visible and death is within 30 days of it, we cannot tell renewal from first year, so we **ask the family** instead of guessing. | |
| Last card use is the later of the card record's last transaction and the last passbook line mentioning ATM, POS, RUPAY, ECOM, CARD, AEPS or BC. | |
| Only one RuPay card per person is claimed. | |

## ⚠ Questions for you

1. **PMSBY lien period (PMSBY-R4).** Only the bank summary says "Risk will be covered after 30 days from the date of entry". Is this in the DFS PMSBY rules (w.e.f. 1.6.2022)? If not, delete `rules/PMSBY-R4.json`, because it could wrongly deny an accident in someone's first month.
2. **RuPay 90-day boundary.** "Within 90 days prior to the date of accident including accident date". We allow a transaction on day 90 (that is, 91 calendar days including the accident day). Should day 90 be excluded?
3. **PMJJBY lien boundary.** "First 30 days from the date of enrolment": we treat day 30 as covered. Confirm.
4. **Age limits.** Both schemes use "age nearer birthday" and end cover at the *annual renewal* after the limit. We simply check age at death ≤ 55 / ≤ 70. Fine for R0?
5. **RuPay payout tiers.** The ₹1 lakh / ₹2 lakh split by 28 Aug 2018 comes from IDFC's page. Does it match NPCI's FY2026-27 program note? The NPCI site blocked our download.
6. **Renewal timing.** We assume renewals are debited in May or June (banks remit by 30 June). Some banks debit renewal premiums later in June or early July. Should July count as a renewal too?
7. **PMJJBY documents.** The rules we hold don't list claim documents. Confirm death certificate + passbook + ID proof against your bank's PMJJBY claim form.
