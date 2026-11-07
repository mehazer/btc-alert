"""Selected-Coin specialist (Phase 8).

Builds a coin-specific thesis from the evidence pack and technical/risk context.
Does not place trades. Emphasizes BTC-beta vs idiosyncratic drivers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from .contracts import AgentOutput, CoinThesis, EvidencePack, RiskAssessment


def evaluate(
    pack: EvidencePack,
    technical: AgentOutput,
    risk: RiskAssessment,
    btc_change_pct: Optional[float] = None,
) -> CoinThesis:
    row = pack.observations[0] if pack.observations else None
    price_chg = row.price_change_pct if row else None
    drivers: List[str] = []
    invalidation: List[str] = list(technical.invalidation[:4])
    flags: List[str] = list(technical.risk_flags)

    if pack.symbol == "BTCUSDT":
        beta = "market-leader"
        drivers.append("BTC is the reference market; moves define beta for alts.")
    elif btc_change_pct is not None and price_chg is not None:
        if abs(btc_change_pct) < 0.3 and abs(price_chg) >= 1.0:
            beta = "idiosyncratic"
            drivers.append(
                f"Price move ({price_chg:+.2f}%) while BTC nearly flat ({btc_change_pct:+.2f}%) suggests coin-specific flow."
            )
        elif (price_chg > 0 and btc_change_pct > 0) or (price_chg < 0 and btc_change_pct < 0):
            beta = "btc-beta"
            drivers.append(
                f"Co-moving with BTC (coin {price_chg:+.2f}%, BTC {btc_change_pct:+.2f}%)."
            )
        else:
            beta = "mixed"
            drivers.append("Direction diverges from BTC; mixed beta/idiosyncratic read.")
    else:
        beta = "unknown"
        drivers.append("Insufficient BTC relative context.")

    if row and row.funding_rate is not None:
        if row.funding_rate >= 0.0003:
            drivers.append("Funding elevated — crowded long risk on this coin.")
            flags.append("elevated-funding")
        elif row.funding_rate <= -0.0003:
            drivers.append("Funding negative — crowded short / squeeze potential.")
            flags.append("negative-funding")

    if row and row.oi_change_pct is not None and abs(row.oi_change_pct) >= 3:
        drivers.append(f"OI change {row.oi_change_pct:+.1f}% — derivatives participation shifting.")

    thesis = (
        f"{pack.symbol}: technical='{technical.state}', risk={risk.overall_level}, "
        f"btc_relationship={beta}."
    )
    if risk.veto:
        flags.append("risk-veto")
        invalidation.append("Risk veto remains active")

    confidence = int(min(75, technical.confidence if pack.quality_status == "pass" else technical.confidence * 0.7))

    return CoinThesis(
        agent="selected-coin",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        thesis=thesis,
        btc_relationship=beta,
        drivers=drivers,
        invalidation=invalidation,
        risk_flags=flags,
        confidence=confidence,
        evidence_ids=[pack.evidence_id] + list(technical.evidence_ids)[:5],
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        model_version="coin-specialist-v0.1",
    )
