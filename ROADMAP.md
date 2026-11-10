# BTC Alert — Improvement Roadmap

Last updated: 2026-11-10

## Completed (do not reopen without new evidence)

| Item | Notes |
|------|--------|
| Multi-coin alert bot + Telegram figures | `alert_bot.py` |
| Intelligence v2 shadow stack | evidence → agents → decision → ledger |
| OI persistence into `SYMBOL.json` | `update_rich_history` |
| Forecast engine with bands + ABSTAIN | `intelligence_v2/forecast.py` |
| Risk agent v0.2 + medium samples | risk/evaluation/ledger |
| Pipeline orchestration 4h | `.github/workflows/pipeline.yml` |
| Development principles doc | `docs/DEVELOPMENT_PRINCIPLES.md` |
| Project + agent dashboards (v1) | `docs/project.html`, `docs/agents.html` |
| Whale agent scaffold (free APIs) | `agents/whale_agent.py` |

## Open / In progress

| Priority | Action | Owner | Status |
|----------|--------|-------|--------|
| Critical | Forecast confluence accuracy (3/6-bar) | AI | Open |
| High | OI series fill via scheduled runs | Automated | Waiting |
| High | News Telegram impact ranking 24h/7d | AI | In progress |
| High | Integrate whale agent into pipeline (shadow) | AI | Open |
| Medium | Drop-risk wording clarity | AI | Open |
| Medium | Main dashboard Market Status (no duplicate BTC price) | AI | Open |
| Critical | Human PR for any production promotion | Human | Blocked by design |

## Technical debt / recurring themes

- Dual history formats (`SYMBOL.json` vs `SYMBOL_4h.json`) — mitigated by evidence normalizer; long-term unify writers.
- Placeholder file push risk — always push full validated content.
- Dashboard Pages lag until pipeline syncs `docs/history/`.

## Free vs paid data

- Prefer free: Binance, OKX public, mempool.space, blockchain.info, alternative.me.
- Paid (not enabled): Whale Alert, CryptoQuant, Glassnode — require explicit human approval.

## Next executable steps

1. Finish news Telegram impact format.
2. Replace main dashboard BTC price card with Market Status.
3. Wire whale agent as optional shadow step (no Telegram until approved).
4. After several 4h runs, revisit forecast rules with OI confluence.
