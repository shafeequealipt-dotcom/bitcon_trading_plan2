# Trading Bot Improvement — Real Implementation Plan

> Goal: improve the bot by proving edge, reducing forced trades and overlapping decision authority, and keeping only components that improve out-of-sample expectancy after realistic trading costs.
>
> Rule: **no component earns production authority because it sounds intelligent. It must demonstrate incremental value on unseen data.**

## Success metrics

Primary metric:

`Expectancy (R) = P(win) × AvgWin(R) − P(loss) × AvgLoss(R) − trading costs(R)`

Always report trade count, net PnL, expectancy, profit factor, max drawdown, average win/loss, turnover, fees/slippage/funding, long/short split, per-regime results, and uncertainty where practical. Win rate is diagnostic, not the optimization target.

# Phase 0 — Freeze Current System

**Objective:** create a reproducible "before" version.

**Implement:** record the production commit; snapshot non-secret production config; record DB schema/execution mode; export closed trades with symbol, side, timestamps, prices, size/leverage, net PnL, fees/funding, scorer/ensemble state, brain decision ID, APEX changes and exit reason; add experiment/run IDs.

**Gate:** the same baseline run must reproduce the same results before optimization begins.

# Phase 1 — Remove Forced Trading Bias

**Objective:** make `NO_TRADE` a normal decision.

**Code:** `src/brain/strategist.py`, brain config and Call-A tests.

Replace prompt language that assumes an opportunity exists or pressures the model to return 2–5 trades. New contract:

> Trade only when evidence supports positive expected return after expected costs. `NO_TRADE` is normal. Never create a trade to satisfy a trade-count target.

Add reason codes: `TRADE`, `NO_TRADE_WEAK_EDGE`, `NO_TRADE_CONFLICT`, `NO_TRADE_LOW_LIQUIDITY`, `NO_TRADE_COST_TOO_HIGH`, `NO_TRADE_STALE_DATA`.

**Tests:** thin/conflicting markets can return zero trades; empty results do not trigger attempts to "find something."

**Gate:** shadow-test against the current prompt. Promote only on better expectancy or comparable expectancy with lower drawdown/turnover/cost.

# Phase 2 — Deterministic Q0 Baseline

**Objective:** determine whether the quantitative core has edge without AI.

Pipeline:

`market data → strategies → TradeScorer → EnsembleVoter → deterministic gate → risk → execution`

Add experiment modes:

- `q0`: quantitative only
- `q1`: Q0 + brain selection
- `q2`: Q1 + APEX
- `q3`: Q2 + adaptive exits

Store complete snapshots for accepted **and rejected** candidates. Rejected setups are data and are required to avoid selection bias.

**Gate:** Q0 must be replayable and produce a complete performance report.

# Phase 3 — Realistic Walk-Forward / Replay Engine

**Objective:** stop evaluating changes from production anecdotes.

Replay chronologically. Never random-shuffle time series. Tune only on past data and test on later untouched data. Maintain a final untouched holdout.

Model real execution: fees, spread, slippage, funding, quantity/tick rounding, min notional, latency and rejected orders.

**Candle rule:** if completed candle T creates the signal, do not assume an impossible fill using information from that same candle. Execute using information available after the signal, normally T+1 or timestamped lower-timeframe data.

**Gate:** explicit leakage tests pass and repeated runs are reproducible.

# Phase 4 — Prove Each Intelligence Layer

Run identical periods/capital through:

| Mode | Quant | Brain | APEX | Adaptive exits |
|---|---|---|---|---|
| Q0 | Yes | No | No | No |
| Q1 | Yes | Yes | No | No |
| Q2 | Yes | Yes | Yes | No |
| Q3 | Yes | Yes | Yes | Yes |

Measure `Q1−Q0` for brain value, `Q2−Q1` for APEX value and `Q3−Q2` for adaptive-exit value.

Persist both original and APEX-modified trades so direction, size, SL and TP changes can be replayed counterfactually.

**Gate:** keep a component only when it repeatedly improves unseen-data expectancy/drawdown across multiple chronological windows.

# Phase 5 — One Exit Authority

**Objective:** eliminate competing exit systems.

Create one `ExitEngine` that resolves proposals from watchdog, deadlines, trailing, ProfitSniper and brain reviews:

`ExitDecision(action, reason, requested_stop, priority, evidence)`

Actions: `HOLD`, `TIGHTEN_STOP`, `PARTIAL_CLOSE`, `CLOSE`.

Hard risk stops have highest priority and AI can never loosen them.

**Gate:** every exit has exactly one final reason and authority.

# Phase 6 — MAE/MFE Exit Research

For every entry calculate MAE, MFE, time-to-MFE, time to +0.5R/+1R/+1.5R/+2R, PnL at 5/10/20/30/60/90 minutes, captured-MFE percentage, and alternative exit outcomes.

Split by side, regime, volatility, setup family and liquidity.

Use this to answer how much adverse movement winners need, how long winners mature, whether timeouts cut winners early, and whether trailing improves expectancy rather than merely win rate.

**Gate:** new SL/TP/trailing/timeout settings must be supported out-of-sample.

# Phase 7 — Remove Strategy Redundancy

Represent each strategy chronologically as `SELL=-1`, `NEUTRAL=0`, `BUY=+1`.

Calculate pairwise correlation, agreement frequency, overlap and incremental expectancy when each strategy is removed/added. Group correlated strategies into evidence families.

Ensemble confidence should reflect independent evidence families, not raw strategy count.

# Phase 8 — Calibrate TradeScorer

Bucket historical candidate scores and measure forward expectancy, PF and drawdown.

Higher scores should generally produce stronger forward expectancy. Run leave-one-component-out tests on base, confluence, context and quality.

**Gate:** do not add new scorer features until existing components demonstrate predictive value.

# Phase 9 — Shrink Small Samples

Do not trust raw per-symbol/per-strategy PF or win rate from small samples.

Use shrinkage such as:

`adjusted_edge = n/(n+k) × observed_edge + k/(n+k) × prior_edge`

Tune `k` only on training data. Store sample size, raw estimate and adjusted estimate. Use adjusted estimates for weighting/sizing.

# Phase 10 — Execution Reality Audit

Record `decision_price`, `requested_price`, `fill_price`, `slippage_bps`, decision-to-submit latency, submit-to-fill latency, fees, funding and rejection reason.

Validate fee selection, spread, slippage vs volatility/size, funding, partial fills, precision, min notional, stop slippage, stale data, duplicate orders, restart recovery and exchange/local reconciliation.

Periodically recalibrate simulation costs from observed execution.

# Phase 11 — AI Provider Independence

State machine:

`LLM healthy → configured Q1/Q2/Q3`

`LLM degraded/unavailable → validated Q0 or safe no-new-entry mode`

Risk protection and position reconciliation remain active regardless of AI availability.

Test timeouts, HTTP 402/403/429/500, malformed JSON and multi-hour provider outages.

# Phase 12 — Controlled Promotion

Promotion path:

`tests → historical walk-forward → shadow → Bybit demo → small controlled live → normal limits`

Define rollback triggers **before** deployment: unexpected drawdown, abnormal rejection/slippage, decision/execution mismatch, duplicate orders, reconciliation failure, stale data or materially worse live expectancy.

Rollback to the last proven configuration instead of immediately adding another rule.

# Implementation Order

`P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12`

**P0–P4 are the critical path. Do not add indicators, strategies or models before those phases are complete.**

# Definition of Done

The project must be able to answer with reproducible evidence:

1. Does Q0 make money after realistic costs on unseen data?
2. What incremental value does the brain add?
3. What incremental value does APEX add?
4. Do adaptive exits beat a simpler exit?
5. Which strategies provide independent information?
6. Does a higher score predict higher expectancy?
7. Are SL/TP/timeouts supported by MAE/MFE?
8. Does simulated execution resemble real execution?
9. Is the system safe with all AI providers unavailable?
10. Can every production change roll back to a proven baseline?

**Complexity is a hypothesis until the data proves it is an edge.**

## Validation requirements added for this implementation

- Historical: multiple regimes and preferably 1–2+ years of available data.
- Shadow: first serious review after 30 days AND roughly 100–200 trades; extend if sample/opportunity coverage is insufficient. Approximately 300–500 trades gives stronger evidence, not automatic proof.
- Demo: at least approximately 100 trades with measured execution behavior.
- Small live: approximately 100–200 real fills before considering normal sizing.
- Compare Q0/Q1/Q2/Q3 on the same opportunities, periods and capital wherever possible; report divergence due to position/capital constraints.
- Freeze active experiments. A material change gets a new version and validation window.
- Report gross returns and costs separately OR net returns without subtracting costs twice. R uses initial monetary stop risk fixed at entry. Missing cost data is unknown, never zero.
- Define acceptable drawdown and promotion/rollback criteria before each trial. No automatic promotion based solely on sample count, elapsed days, or win rate.
- Current implementation evidence and open gates: `docs/validation/phase-0-1.md`.
