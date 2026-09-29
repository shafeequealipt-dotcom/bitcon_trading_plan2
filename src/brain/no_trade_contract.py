"""Versioned, opt-in removal of Call-A trade pressure; no new market signals."""

from __future__ import annotations

REASON_CODES = frozenset(
    {
        "TRADE",
        "NO_TRADE_WEAK_EDGE",
        "NO_TRADE_CONFLICT",
        "NO_TRADE_LOW_LIQUIDITY",
        "NO_TRADE_COST_TOO_HIGH",
        "NO_TRADE_STALE_DATA",
    }
)
CONTRACT_VERSION = "no-trade-v1"
CONTRACT = """Trade only when evidence supports positive expected return after expected costs.
NO_TRADE is a normal, successful decision in any market regime. Never create a
trade to satisfy a trade-count target. There is no minimum trade count, and an
entire cycle may correctly return zero trades. Do not assume an exploitable
opportunity exists. Low liquidity, conflicting evidence, weak edge, stale data,
or expected costs consuming the plausible edge are reasons to abstain.

Evaluate each candidate using the supplied evidence and realistic round-trip
fees, spread, slippage and funding for the expected holding period. Do not invent
cost estimates, win probabilities or positive expectancy when evidence is absent.
A high score, regime label or attractive reward/risk ratio alone does not prove
positive expectancy. Missing cost/edge evidence supports NO_TRADE_WEAK_EDGE.
Reduced size does not turn negative expectancy into positive expectancy.

QUALITY OVER QUOTA: Return new_trades: [] when no candidate qualifies. Do not
retry, seek a replacement trade, or relax criteria because the list is empty.
Use a top-level reason_code: TRADE for a nonempty list; otherwise choose exactly
one of NO_TRADE_WEAK_EDGE, NO_TRADE_CONFLICT, NO_TRADE_LOW_LIQUIDITY,
NO_TRADE_COST_TOO_HIGH, NO_TRADE_STALE_DATA. Explain the evidence in market_view.
For mixed selections, explain rejected candidates in market_view as well.
This decision contract takes precedence over any contextual coaching to trade.
"""


def build_no_trade_prompt(control: str) -> str:
    """Keep operational instructions while replacing premise and count pressure.

    Fail loudly if the frozen control layout changes; never fall back to its
    aggressive opening under the trial flag. Both legacy variants are supported.
    """
    marker = "DIRECTION BY REGIME"
    if marker not in control or "\nRULES:\n1. Return the 2 to 5" not in control:
        raise ValueError("Control prompt layout changed; version the no-trade contract")
    body = control[control.index(marker) :]
    lines = body.splitlines()
    lines = [
        "1. " + CONTRACT.splitlines()[0] + " Zero trades is valid; no count target."
        if line.startswith("1. Return the 2 to 5")
        else line
        for line in lines
    ]
    body = "\n".join(lines)
    body = body.replace(
        "When the higher-RR side lacks confirmation and the confirmed side lacks room, "
        "prefer the CONFIRMED side at SMALL size with an early trailing target to ride "
        "momentum into open space — skip only when BOTH sides genuinely lack confirmation.",
        "When the higher-RR side lacks confirmation and the confirmed side lacks room, "
        "NO_TRADE_CONFLICT is valid; do not force either side.",
    )
    body = body.replace(
        " — small size on a short hold is how you take more genuine plays without "
        "over-committing to any one read.",
        ". Reduced size never substitutes for edge.",
    )
    body = body.replace(
        " Shorter holds turn the book over and surface more genuine plays per session",
        "",
    )
    body = body.replace('{"new_trades":[', '{"reason_code":"TRADE","new_trades":[')
    return (
        CONTRACT + "\n" + body + '\nValid abstention: {"reason_code":"NO_TRADE_WEAK_EDGE",'
        '"new_trades":[],"market_view":"Insufficient evidence after costs"}\n'
    )


def parse_reason(data: dict) -> str:
    """An abstention is distinct from a malformed/provider-error response."""
    if not isinstance(data, dict) or not isinstance(data.get("new_trades"), list):
        raise ValueError("new_trades must be an explicit list")
    trades, reason = data["new_trades"], data.get("reason_code")
    if not isinstance(reason, str) or reason not in REASON_CODES:
        raise ValueError("Missing or invalid reason_code")
    if bool(trades) != (reason == "TRADE"):
        raise ValueError("reason_code contradicts new_trades")
    if any(
        not isinstance(trade, dict)
        or not trade.get("symbol")
        or trade.get("direction") not in {"Buy", "Sell"}
        for trade in trades
    ):
        raise ValueError("Invalid trade directive")
    return reason
