# Eval scorecard: 4/4 cases pass

Mode: documents.json fixtures, no model; LLM_MODE=replay.

| Case | Covers | Rules | Payout | Missing docs | Packets | Result |
|---|---|---|---|---|---|---|
| case_a | 4/4 | 3/3 | 3/3 | 3/3 | 3/3 | PASS |
| case_b | 4/4 | 3/3 | 3/3 | 3/3 | 3/3 | PASS |
| case_c | 4/4 | 3/3 | 3/3 | – | 3/3 | PASS |
| case_none | 1/1 | – | – | – | – | PASS |

## case_a

- PASS covers found ['pmjjby', 'pmsby', 'rupay'] (expected ['pmjjby', 'pmsby', 'rupay'])
- PASS pmjjby evidence strong (expected strong)
- PASS pmsby evidence strong (expected strong)
- PASS rupay evidence strong (expected strong)
- PASS pmjjby eligible PMJJBY-R1, PMJJBY-R2, PMJJBY-R3
- PASS pmjjby payout Rs 200,000 (expected Rs 200,000)
- PASS pmjjby missing documents none
- PASS pmjjby packet passes, every field filled and traced
- PASS pmsby eligible PMSBY-R1, PMSBY-R2, PMSBY-R3, PMSBY-R4
- PASS pmsby payout Rs 200,000 (expected Rs 200,000)
- PASS pmsby missing documents none
- PASS pmsby packet passes, every field filled and traced
- PASS rupay eligible RUPAY-R1, RUPAY-R2
- PASS rupay payout Rs 200,000 (expected Rs 200,000)
- PASS rupay missing documents none
- PASS rupay packet passes, every field filled and traced

## case_b

- PASS covers found ['pmjjby', 'pmsby', 'rupay'] (expected ['pmjjby', 'pmsby', 'rupay'])
- PASS pmjjby evidence strong (expected strong)
- PASS pmsby evidence strong (expected strong)
- PASS rupay evidence strong (expected strong)
- PASS pmjjby eligible PMJJBY-R1, PMJJBY-R2, PMJJBY-R3
- PASS pmjjby payout Rs 200,000 (expected Rs 200,000)
- PASS pmjjby missing documents ['death_certificate']
- PASS pmjjby packet blocked with the reason (1 blocker(s))
- PASS pmsby eligible PMSBY-R1, PMSBY-R2, PMSBY-R3, PMSBY-R4
- PASS pmsby payout Rs 200,000 (expected Rs 200,000)
- PASS pmsby missing documents ['death_certificate']
- PASS pmsby packet blocked with the reason (1 blocker(s))
- PASS rupay unknown RUPAY-R2
- PASS rupay payout Rs 100,000 (expected Rs 100,000)
- PASS rupay missing documents ['death_certificate']
- PASS rupay packet blocked with the reason (3 blocker(s))

## case_c

- PASS covers found ['pmjjby', 'pmsby', 'rupay'] (expected ['pmjjby', 'pmsby', 'rupay'])
- PASS pmjjby evidence strong (expected strong)
- PASS pmsby evidence strong (expected strong)
- PASS rupay evidence strong (expected strong)
- PASS pmjjby not_eligible PMJJBY-R2
- PASS pmjjby payout Rs 200,000 (expected Rs 200,000)
- PASS pmjjby packet blocked with the reason (1 blocker(s))
- PASS pmsby not_eligible PMSBY-R2
- PASS pmsby payout Rs 200,000 (expected Rs 200,000)
- PASS pmsby packet blocked with the reason (4 blocker(s))
- PASS rupay not_eligible RUPAY-R1, RUPAY-R2
- PASS rupay payout Rs 100,000 (expected Rs 100,000)
- PASS rupay packet blocked with the reason (5 blocker(s))

## case_none

- PASS covers found [] (expected [])
