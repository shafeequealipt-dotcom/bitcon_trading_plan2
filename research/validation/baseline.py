"""Read-only baseline capture. Run with python -m research.validation.baseline.

Missing data stays unknown. A capture is not a backtest or proof of edge.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import tomllib
from pathlib import Path

from src.validation.experiment import canonical, digest, redact

TABLES = (
    "schema_version",
    "trade_history",
    "trade_log",
    "trade_intelligence",
    "strategy_trades",
    "ensemble_votes",
    "claude_decisions",
    "brain_decisions",
    "orders",
    "regime_history",
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def read_database(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    # A read transaction covers schema AND every table, including a live WAL DB.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        schema = [
            dict(r)
            for r in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "ORDER BY type,name"
            )
        ]
        names = {r["name"] for r in schema if r["type"] == "table"}
        tables = {}
        for name in TABLES:
            if name in names:
                tables[name] = sorted(
                    (dict(r) for r in db.execute(f'SELECT * FROM "{name}"')), key=canonical
                )
        db.rollback()
    return schema, tables


def closed_records(tables):
    """Keep ledgers separate; never silently deduplicate or join by symbol/time.

    PnL is exported under its original name; existing ledgers do not reliably
    establish net-of-all-costs semantics. No invented fees, R, or decision IDs.
    """
    result = []
    for table, time_key in (("trade_history", "exit_time"), ("trade_log", "closed_at")):
        for row in tables.get(table, []):
            if not row.get(time_key):
                continue
            tid, mode = row.get("trade_id"), row.get("exchange_mode")
            related = [
                r
                for r in tables.get("trade_intelligence", [])
                if tid
                and r.get("trade_id") == tid
                and (mode is None or r.get("exchange_mode") in (None, mode))
            ]
            result.append(
                {
                    "source_table": table,
                    "trade_id": tid,
                    "exchange_mode": mode,
                    "symbol": row.get("symbol"),
                    "side": row.get("side", row.get("direction")),
                    "entry_time": row.get("entry_time", row.get("opened_at")),
                    "exit_time": row[time_key],
                    "entry_price": row.get("entry_price"),
                    "exit_price": row.get("exit_price"),
                    "qty": row.get("qty"),
                    "size_usd": row.get("size_usd"),
                    "leverage": row.get("leverage"),
                    "reported_pnl": row.get("pnl", row.get("pnl_usd")),
                    "net_pnl": row.get("net_pnl"),
                    "fees": row.get("fees"),
                    "funding": row.get("funding"),
                    "slippage": row.get("slippage"),
                    "brain_decision_id": row.get("brain_decision_id"),
                    "exit_reason": row.get("close_reason"),
                    "related_intelligence": related,
                    "original_row": row,
                }
            )
    return sorted(result, key=lambda r: (r["exit_time"], r["source_table"], str(r["trade_id"])))


def capture(
    root: Path,
    config: Path,
    output: Path,
    *,
    experiment_id: str,
    run_id: str,
    execution_mode: str,
    db_path: Path | None = None,
    effective_config: Path | None = None,
):
    root = root.resolve()
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Commit all source changes before baseline capture")
    if not experiment_id.strip() or not run_id.strip() or not execution_mode.strip():
        raise ValueError("experiment_id, run_id and actual execution_mode are required")
    config_data = redact(tomllib.loads(config.read_text()))
    effective = redact(json.loads(effective_config.read_text())) if effective_config else None
    schema, tables = read_database(db_path) if db_path else ([], {})
    closed = closed_records(tables)
    source = {
        "commit": git(root, "rev-parse", "HEAD"),
        "tree": git(root, "rev-parse", "HEAD^{tree}"),
    }
    payloads = {
        "config.redacted.json": config_data,
        "effective_config.redacted.json": effective,
        "schema.json": schema,
        "tables.json": tables,
        "closed_trades.json": closed,
        "environment.json": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": sorted(
                (d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
            ),
        },
    }
    coverage = {
        k: sum(r[k] is not None for r in closed)
        for k in ("net_pnl", "fees", "funding", "slippage", "brain_decision_id", "exit_reason")
    }
    blockers = [
        "Baseline decision/fill replay is not implemented until Phases 2-3",
        "LLM reruns are not deterministic; preserve original responses for replay",
    ]
    if db_path is None:
        blockers.append("No production database supplied; this is a source-only capture")
    if effective is None:
        blockers.append("Effective runtime configuration/environment overrides not supplied")
    manifest = {
        "format_version": 1,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "execution_mode": execution_mode,
        "source": source,
        "declared_production_commit": None,
        "capture_tool_sha256": digest(Path(__file__).read_text()),
        "input_fingerprint": digest(
            {
                "source": source,
                "config": config_data,
                "effective_config": effective,
                "schema": schema,
                "tables": tables,
                "execution_mode": execution_mode,
                "environment": payloads["environment.json"],
            }
        ),
        "files": {name: digest(value) for name, value in payloads.items()},
        "row_counts": {name: len(rows) for name, rows in tables.items()},
        "missing_tables": sorted(set(TABLES) - tables.keys()),
        "cost_and_attribution_coverage": coverage,
        "ledger_policy": "Separate source ledgers; do not aggregate both as unique trades",
        "gate": "NOT_VALIDATED",
        "blockers": blockers,
    }
    output.mkdir(parents=True, exist_ok=False)  # A frozen run is never overwritten.
    for name, value in {**payloads, "manifest.json": manifest}.items():
        (output / name).write_text(canonical(value) + "\n")
    return manifest


def verify(output: Path):
    manifest = json.loads((output / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid capture filename")
        if digest(json.loads((output / name).read_text())) != expected:
            raise ValueError(f"Capture changed: {name}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capture")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--db", type=Path)
    p.add_argument("--effective-config", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--execution-mode", required=True)
    p = sub.add_parser("verify")
    p.add_argument("capture", type=Path)
    p = sub.add_parser("compare")
    p.add_argument("first", type=Path)
    p.add_argument("second", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        result = capture(
            args.repo,
            args.config,
            args.output,
            experiment_id=args.experiment_id,
            run_id=args.run_id,
            execution_mode=args.execution_mode,
            db_path=args.db,
            effective_config=args.effective_config,
        )
        print(canonical(result))
    elif args.command == "verify":
        verify(args.capture)
        print("Capture integrity verified; trading reproducibility gate remains separate.")
    else:
        a, b = verify(args.first), verify(args.second)
        if a["input_fingerprint"] != b["input_fingerprint"]:
            raise SystemExit("DIFFERENT baseline inputs")
        print("Identical captured baseline inputs; this is not a strategy replay result.")


if __name__ == "__main__":
    main()
