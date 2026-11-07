# Intelligence v2 migration and rollback plan

## Current state

The legacy system remains the production system. v2 runs in shadow mode (manual or scheduled), writes only under `history/intelligence_v2/`, and never sends Telegram or submits orders.

## Phase 8 implemented

- **Interpreter**: evidence-ID grounded deterministic narrative (`intelligence_v2/interpreter.py`); cites only known evidence IDs; no required LLM API.
- **Selected-Coin agent**: per-symbol thesis with BTC-beta vs idiosyncratic drivers (`coin_agent.py`).
- **Portfolio agent**: paper book from `history/intelligence_v2/paper_portfolio.json`; concentration and decision alignment warnings; `execution_allowed=false`.
- **Promotion gate**: scorecard thresholds evaluated into `promotion_report.json`; status is `not_ready` or `ready_for_human_review`; **`auto_promote` is always false**.
- Shadow artifact includes interpretations, coin theses, portfolio, and promotion report.

## Promotion criteria (human review only)

- Settled evaluation rows >= 30
- Direction accuracy >= 55%
- Technical samples >= 15
- Risk samples >= 10
- False-calm rate <= 35% (when available)
- Explicit human PR approval still required; no automatic production switch

## Rollback

Disable the shadow workflow schedule or stop running it. Production `alert_bot.py` path is unchanged.

## Phase 9 next (optional)

LLM-assisted interpreter behind an API key (still evidence-grounded), multi-venue live adapters, and a formal promotion PR checklist UI on the dashboard.
