# Rules as code

One JSON-logic decision table per rule (M3), effective-dated and versioned in git. Each rule
lists the facts it `requires`; when any is missing the evaluator returns `unknown` with
`missing_facts` instead of guessing. Every result cites its rule ID and source circular.
