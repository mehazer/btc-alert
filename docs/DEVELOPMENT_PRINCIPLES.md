# BTC Alert — Development Principles (Persistent Knowledge)

Apply to all future development, maintenance, and processing in this repository.

## Rules

1. **Token efficiency** — Optimize token use. Do not re-review the entire repo, all docs, or all prior decisions when relevant context is unchanged.
2. **Targeted inspection** — Identify affected components, dependencies, and prior implementation first. Inspect only what is needed unless accuracy requires a broader review.
3. **Accuracy first** — Token optimization must never compromise correctness, reliability, or security.
4. **Repository consistency** — After every implementation, update related code and supporting files to avoid drift.
5. **Documentation sync** — Update README, architecture notes, roadmaps, and config references when behavior changes.
6. **End-to-end implementation** — Do not ship isolated changes without considering system impact (pipeline, ledger, dashboards, Telegram).
7. **Closed-loop cycle** — Inspect → requirements → implement → test → update related code/docs → update dashboards/action status → record outstanding issues and next steps.
8. **Persistence** — These principles are project knowledge; apply them automatically on future tasks.

## Execution report (every run)

After every development or processing run, report:

- What was implemented
- Files/components modified
- Tests and results
- Dashboard changes
- Actions completed / open / blocked
- Human intervention required
- Waiting on scheduled runs
- Recommended next steps

### Hard rules

- Do not mark untested work completed
- Do not mark complete only because code was generated
- Do not ignore failed executions
- Do not overwrite working functionality without validation
- Do not introduce paid services without explicit human approval
- Do not expand scope unless required to prevent a technical conflict
