# Phase 0–1 implementation and validation

## Frozen source

Original repository: `shafeequealipt-dotcom/BitCoin_Trading`.
Original commit: `0e3052bf004af613901619596a4786989f378478`.
Copied baseline commit: `106fa9d29eda691fa37517323ba5b104afe88adf`.
Both have tree `e22bb0632a8d59201bbeda2340ae31962da4263f` (1,988 identical tracked files/modes).
Rollback branch in the new repository: `baseline/original-0e3052b`.
Original ancestry remains in the original repository; the destination holds an exact source snapshot, not imported ancestry.

This is the repository baseline, **not a verified deployed production commit**.
No production DB, effective deployment configuration, fill history, or recorded LLM responses were supplied. No financial performance claim or promotion is justified.

## Phase 0 capture

Run in a clean committed checkout using Python 3.11+ (stdlib only):

```bash
python -m research.validation.baseline capture \
  --config config.toml --db /path/to/production-copy.db \
  --effective-config /path/to/effective-settings.json \
  --execution-mode ACTUAL_MODE --experiment-id baseline-v1 --run-id capture-001 \
  --output validation_runs/baseline-v1/capture-001
python -m research.validation.baseline verify validation_runs/baseline-v1/capture-001
```

`--effective-config` should contain the resolved runtime settings (for example `dataclasses.asdict(settings)` from the actual deployment), including non-secret environment overrides. Credentials are redacted. The configured TOML alone does not prove the runtime settings. Record the deployed commit separately and check it matches the captured source commit. Omit `--db` for a clearly marked source-only capture. Missing DB paths fail without creating an empty database.

Capture uses a read-only SQLite transaction including WAL-visible committed data. It writes canonical config, actual schema DDL, raw relevant tables, closed-ledger records, runtime package versions, source hashes, coverage diagnostics and checksums into a new directory. Existing runs cannot be overwritten. It never places orders, imports the trading service, or migrates the input DB. The declared execution mode is an operator assertion; mixed-mode ledgers retain their mode columns and are not silently filtered.

Re-capture the same frozen DB/config with a different run ID and output directory, then:

```bash
python -m research.validation.baseline compare validation_runs/baseline-v1/capture-001 validation_runs/baseline-v1/capture-002
```

This proves identical captured inputs only. The Phase 0 strategy-result reproducibility gate remains open until recorded decision/fill replay exists in Phases 2–3. Rerunning a live LLM is not deterministic.

`trade_history` and `trade_log` are separate ledgers, not two sets of unique trades to sum. Intelligence/APEX rows join only on exact trade ID, with mode compatibility; unmatched or ambiguous rows remain visible. Raw scores, ensemble vote setup IDs and decision tables are retained. Missing fees, slippage, funding, net-PnL semantics, initial stop risk and brain-decision joins block a defensible expectancy calculation. The exporter never substitutes zero or guesses joins from symbol/time.

Keep captures and journals private under ignored `validation_runs/`; they contain account/trade history. Do not upload production data to this public repository.

## Phase 1 trial contract

The control remains the default. Set `[brain].no_trade_contract_enabled = true` only in an isolated experiment configuration. It takes precedence over both existing Call-A prompt variants and replaces their forced-action premise and quota. It is not a validated Q0 mode and does not disable APEX/exits.

The new response includes `reason_code`: `TRADE`, `NO_TRADE_WEAK_EDGE`, `NO_TRADE_CONFLICT`, `NO_TRADE_LOW_LIQUIDITY`, `NO_TRADE_COST_TOO_HIGH`, or `NO_TRADE_STALE_DATA`. Empty lists are successful decisions; malformed/inconsistent contracts are failures. Neither is retried to manufacture entries. Existing urgent position actions are retained on valid no-trade responses; Call B remains unchanged.

For auditable trials set:

```toml
[brain]
no_trade_contract_enabled = true
validation_experiment_id = "p1-no-trade-v1"
validation_run_id = "shadow-run-001"
validation_audit_dir = "validation_runs/decisions"
```

Start from a clean committed checkout (journaling rejects unversioned source/config changes). Use a new run ID on restart and a new experiment ID for changes to code/config/prompt. Journals capture exact input and output text, prompt hashes, configured model/settings, decision ID and reason code. `opportunity_id` hashes the supplied user-context string: feed both arms exactly the same frozen context for paired comparison. It is not a replacement for Phase 2's full candidate snapshot or actual provider/fallback model metadata.

For control runs use the same audit settings with a separate experiment ID and the flag false. Journal failures fail the affected Call A (no new plan); they do not change the independent watchdog or Call B. Without an audit directory, journaling is disabled and legacy behavior is preserved. Do not claim unaudited runs satisfy validation.

The existing combined legacy `create_strategic_plan` API is not the active Call-A path; trial flag usage through that API is rejected rather than silently applying the old forced-trading contract.

## Promotion and rollback

No phase gate is passed by unit tests alone. Follow the sample/regime targets in `plan.md`; pre-register drawdown limits and cost assumptions. Freeze every active window. The required report includes sample count, expectancy in R, net PnL, PF, max drawdown, average win/loss, fees, spread/slippage, funding, turnover, side and regime breakdowns, and uncertainty. This change supplies infrastructure, not historical/shadow results.

Prompt-only rollback: set `no_trade_contract_enabled = false` in a **new** experiment configuration. Full source rollback: check out `baseline/original-0e3052b` in a separate checkout and restore that deployment's compatible configuration. Do not switch source while an execution process is running; reconcile existing positions under the operational runbook. No DB migrations are introduced by these phases.

## Open gates

- Production capture and deployed-commit verification: pending operator data.
- Baseline decision/fill replay: pending Phase 2–3.
- Paired current-prompt versus no-trade historical/shadow evaluation: pending data/framework.
- Demo/small-live/normal-live promotion: not authorized by any evidence from this implementation.

## Verified test results

Python 3.12. Baseline targeted suite: 181 passed, 6 failed. Same suite plus 45 new
tests: 226 passed, the identical 6 failures. Another 17 prompt-extension,
compression and cycle regressions passed. Total changed-checkout result: 243
passed, 6 pre-existing failures; zero new failures in the tested set.
The full repository suite was not run. No network model/exchange calls were
made by these tests. `test-results.json` lists exact baseline failure IDs.
The existing failures concern prompt-size limits and stale count/skip wording;
we leave the frozen control and its tests intact for review.

Reproduce using Python 3.12 and `test-requirements.txt` in an isolated venv:

```bash
python -m pytest tests/test_expectancy_program tests/test_strategist_calla_skip.py tests/test_call_a_hang_guard.py tests/test_strat_call_pairing.py tests/test_strategist_callb_prompt.py tests/test_brain_thesis_invalidation_parsing.py tests/test_call_a_thesis_invalidation_prompt.py tests/test_layer2_defect2_brain_decisions.py tests/test_phase0/test_settings.py tests/test_stage2_phase3 -q
python -m pytest tests/test_phase6_1d_briefing/test_prompt_extension_flag.py tests/test_phase4_layer1_restructure/test_cold_start_resume_enforcement.py tests/test_strategist_compression -q
```

The dependency snapshot is the **test environment**, not an assertion of deployed
production dependencies; the original `requirements.freeze.txt` is unchanged.

The source-only CLI capture was also run twice against the exact copied baseline;
`compare` verified identical captured inputs. No production database was involved.
`source-capture.json` records the input fingerprint and unresolved gates.
