# Demo script (about 5 minutes)

Follows SRS section 8.1. Everything shown is synthetic: the Kaveri Gramin Bank, the
people (Ramesh, Sunita, Suresh and Kavita Sample) and every document are invented and
stamped SPECIMEN.

## Before you record (10 minutes)

1. Start Docker Desktop from the Start menu. Check `.env` has `MODEL_API_KEY` set.
2. Clear old cases, start the app with the live models, and prepare case C:

   ```bash
   uv run poe demo-reset
   ```

   ```bash
   uv run poe demo
   ```

   In a second terminal, once the console is up:

   ```bash
   uv run poe demo-case c
   ```

   Keep the `http://localhost:5173/?case=...` link it prints; that is case C for step 8.
3. Do one full dry run of steps 2–7 with exactly the lines below, then
   `uv run poe demo-reset` and `uv run poe demo-case c` again. `poe demo` records every
   model answer under `data/demo-recordings`, so on the recorded run an identical request
   gets the same answer back in under a second. A line typed differently goes to the live
   model (5–10 seconds per chat turn, about 30 seconds to read the photos).
4. Open three browser tabs: the console (`http://localhost:5173`), the review queue
   (`/review`) and the case C link. Put the case A photos
   (`evals/cases/case_a/documents/d1.jpg` … `d5.jpg`) in an easy-to-reach folder.
5. On the review queue, type the reviewer name `Asha (claims desk)` once (it is remembered).

## The run

Times are what the second rehearsal measured from the audit log, plus talking.

### 1. The problem (0:00–0:15)

> "When someone dies in India, their family is often owed lakhs from insurance they never
> knew about: ₹436 a year for PMJJBY, ₹20 for PMSBY, the free cover on a RuPay Jan Dhan
> card. The evidence is sitting in the passbook. Suraksha Setu finds it, proves it and
> files the claim, with a person approving every step that matters."

### 2. Intake (0:15–1:15)

Console tab, press **Start**.

- Point at the first message: condolence, *free*, *nobody will ask for money, OTP or PIN*,
  and what the data is used for.
- Type `Sunita Sample` in the consent box, press **I agree**. (Consent is recorded and the
  name goes straight into the encrypted vault.)
- Press **Upload photos** and pick all five case A photos. While they are read, keep
  typing:
  1. `I am his wife and the nominee. My husband Ramesh Sample died in a road accident in Mysuru on 14 September 2026. He was 41.`
  2. `Yes. He had a savings account at Kaveri Gramin Bank and a RuPay Jan Dhan card. I have uploaded the photos.`
  3. `He used it at the ATM a few days before the accident.`
  4. `Yes, all correct.`

> "The conversation agent is Muse Spark. It never sees a name, only vault tokens, and it
> reads back what it understood before anything happens."

### 3. Documents (1:15–1:45)

Scroll the Documents panel.

- Every field shows the confidence Muse Spark gave it.
- ID proof: **Aadhaar masked**, `XXXX XXXX 4821`. The full number was blurred out of the
  image before any model saw it.
- Passbook holder and death certificate show the *same* token: the system can tell the
  names match without knowing them.

### 4. Discovery (1:45–2:15)

Press **Finish and check covers**. The briefing appears in about 2 seconds.

> "Three covers from one passbook: the ₹436 debit is PMJJBY, the ₹20 debit is PMSBY, and
> the ATM withdrawal four days before the accident keeps the RuPay cover alive."

Point at the evidence line under each cover (date, description, amount, *passbook*).

### 5. Eligibility (2:15–2:35)

All three say **eligible** with rule IDs. Click the **RUPAY-R2** chip:

> "Every decision cites a rule. This is the RuPay 90-day rule: its version, the date it is
> in force from, the NPCI document it comes from, and the exact logic. The rules are code,
> not model output."

Press **Start these 3 claims**.

### 6. Packets (2:35–3:20)

Switch to the review queue tab. Within about 30 seconds the three claims show
**Needs your approval** with **passed** verifier badges. Open **RuPay card accident cover**.

- Left: the printable packet. Every field says where it came from (registry, passbook,
  death certificate).
- Right: the coordinator's summary and the verifier: deterministic checks plus a second
  opinion from Muse Wonder (a different model from the one that filled the form).

### 7. Human gate (3:20–4:00)

Press **Approve and file v1**.

> "Nothing leaves without a named person approving this exact version."

The mock claim desk returns `MOCK-RUPAY-…`; the stage moves to **With the claim desk**,
then to **Confirm payout** in about 15 seconds. Press **Confirm**: **Paid**.
(Approve PMJJBY and PMSBY from the queue the same way, or skip them on camera.)

### 8. Case C and the audit log (4:00–4:50)

Case C tab (prepared earlier):

> "Not every family is owed money, and we say so plainly. Suresh died of an illness: PMSBY
> and RuPay only cover accidents, and he joined PMJJBY 15 days before he died, inside the
> 30-day waiting period. Each reason cites its rule."

Back on case A, click **Audit log**, then **Verify hash chain**:

> "Every step: who did it, which model and prompt version, which rule versions, every
> approval. Append-only, hash-chained, and no names or numbers in it."

### 9. Close (4:50–5:00)

> "Suraksha Setu: Meta's Muse models doing the reading and writing, rules deciding, a
> person approving. Built for the families who need it most."

## If something goes wrong

| Symptom | Fix |
|---|---|
| Chat says it is having trouble responding | The Meta API is slow or down. Wait 10 seconds and resend, or switch to the recorded dry run. |
| A photo stays on "Reading" for over a minute | Reload the page; read photos stay. Upload the missing one again. |
| Packets stay on "Building packet" | Check the worker is running (`poe demo` output). Temporal UI: `http://localhost:8233`. |
| Payout never arrives | The mock portal must be running on port 8100. |
| Wrong or old cases in the queue | `uv run poe demo-reset`, then reload the console. |

## Rehearsal log

| Run | Date | Result |
|---|---|---|
| 1 | 2026-10-28 | Full case A path in about 5:30, including tool overhead and a 60 s payout wait. Found and fixed: a race on the first chat request, the consent prompt, random name tokens making the verifier flag every name, an upload deadlock under parallel uploads, the speaker not counted as nominee, and the 30 s payout poll (now 5 s in `poe demo`). |
| 2 | 2026-10-28 | All fixes in. System time from the audit log: intake 85 s (photos read in parallel with the chat), briefing 2 s, three packets built and verified 18–37 s after the choice with no warnings, approve to paid about 30 s. With talking, about 4:45. |
