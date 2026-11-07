"""Optional LLM enrichment for the interpreter (Phase 9).

Only runs when an API key env var is present. Output is validated so that
any cited evidence IDs must be a subset of the known set. On any failure,
returns None and the deterministic interpretation stands alone.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import InterpretationReport


def _provider() -> Optional[str]:
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY"):
        return "xai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 30) -> Optional[dict]:
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def _extract_text(provider: str, data: dict) -> Optional[str]:
    try:
        if provider in {"openai", "xai"}:
            return data["choices"][0]["message"]["content"]
        if provider == "anthropic":
            return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _validate_evidence_ids(text: str, allowed: List[str]) -> bool:
    found = set(re.findall(r"evidence-[a-f0-9]{8,}", text))
    return found.issubset(set(allowed))


def enrich(report: InterpretationReport) -> Optional[str]:
    """Return an enriched summary string or None if skipped/failed."""
    provider = _provider()
    if not provider:
        return None

    allowed = list(report.evidence_ids)
    system = (
        "You are a crypto market analyst assistant. Rewrite the structured findings "
        "into 4-6 concise sentences. You MUST only refer to evidence IDs from the provided list. "
        "Do not invent prices, news, or evidence IDs. If information is missing, say unknown. "
        "Do not give trade instructions or position sizes."
    )
    user = json.dumps({
        "symbol": report.symbol,
        "summary": report.summary,
        "bullets": report.bullets,
        "conflicts": report.conflicts,
        "unknowns": report.unknowns,
        "allowed_evidence_ids": allowed,
    }, ensure_ascii=False)

    if provider == "openai":
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            {
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
    elif provider == "xai":
        key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
        data = _post_json(
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": os.environ.get("XAI_MODEL", "grok-2-latest"),
                "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
    else:
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
                "max_tokens": 400,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )

    if not data:
        return None
    text = _extract_text(provider, data)
    if not text or not _validate_evidence_ids(text, allowed):
        return None
    return text.strip()
