import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from research.validation.baseline import capture, closed_records, read_database, verify
from src.core.experiment import DecisionJournal, redact


def test_redaction_nested_and_token_budget():
    value = {"api_key": "SECRET", "nested": [{"password": "PRIVATE"}],
             "max_tokens": 4000, "url": "https://a:b@example.com", "token": "hidden"}
    redacted = redact(value)
    assert "SECRET" not in json.dumps(redacted)
    assert "PRIVATE" not in json.dumps(redacted)
    assert redacted["max_tokens"] == 4000
    assert redacted["url"] == "[REDACTED_URL]"
    assert value["api_key"] == "SECRET"


def database(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE trade_log (trade_id TEXT, symbol TEXT, closed_at TEXT, pnl_usd REAL)")
        db.execute("INSERT INTO trade_log VALUES ('x','BTCUSDT','2026-01-01',12)")
        db.execute("INSERT INTO trade_log VALUES ('open','ETHUSDT','',0)")
        db.execute("CREATE TABLE trade_intelligence (trade_id TEXT, entry_score REAL, apex_flipped INTEGER)")
        db.execute("INSERT INTO trade_intelligence VALUES ('x',80,1)")


def test_export_preserves_unknown_costs_and_exact_join(tmp_path):
    path = tmp_path / "trades.db"
    database(path)
    original = path.read_bytes()
    schema, tables = read_database(path)
    records = closed_records(tables)
    assert len(records) == 1
    assert records[0]["reported_pnl"] == 12
    assert records[0]["net_pnl"] is None
    assert records[0]["fees"] is None
    assert records[0]["related_intelligence"][0]["entry_score"] == 80
    assert records[0]["related_intelligence"][0]["apex_flipped"] == 1
    assert schema and path.read_bytes() == original
    assert read_database(path) == (schema, tables)


def test_missing_db_does_not_create_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_database(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()


def test_ledgers_not_silently_combined_or_guessed():
    tables = {"trade_log": [{"trade_id": "x", "closed_at": "1", "exchange_mode": "shadow"}],
              "trade_history": [{"trade_id": "x", "exit_time": "1"}],
              "trade_intelligence": [{"trade_id": "x", "exchange_mode": "live"}]}
    rows = closed_records(tables)
    assert len(rows) == 2
    assert next(r for r in rows if r["source_table"] == "trade_log")["related_intelligence"] == []


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    def run(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    run("init")
    (root / "config.toml").write_text('[general]\nmode="shadow"\n[brain]\napi_key="private"\n')
    run("add", ".")
    run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return root


def test_capture_reproducible_and_frozen(repo, tmp_path):
    db = tmp_path / "source.db"
    database(db)
    kwargs = dict(experiment_id="baseline", execution_mode="shadow", db_path=db)
    a = capture(repo, repo / "config.toml", tmp_path / "a", run_id="a", **kwargs)
    b = capture(repo, repo / "config.toml", tmp_path / "b", run_id="b", **kwargs)
    assert a["input_fingerprint"] == b["input_fingerprint"]
    assert a["files"] == b["files"]
    assert a["gate"] == "NOT_VALIDATED"
    assert verify(tmp_path / "a") == a
    assert "private" not in (tmp_path / "a/config.redacted.json").read_text()
    with pytest.raises(FileExistsError):
        capture(repo, repo / "config.toml", tmp_path / "a", run_id="a", **kwargs)
    (tmp_path / "a/closed_trades.json").write_text("[]")
    with pytest.raises(ValueError, match="changed"):
        verify(tmp_path / "a")


def test_dirty_checkout_rejected(repo, tmp_path):
    (repo / "change.py").write_text("# changed")
    with pytest.raises(ValueError, match="Commit"):
        capture(repo, repo / "config.toml", tmp_path / "a", experiment_id="x", run_id="a",
                execution_mode="shadow")


def config(run="a", temperature=0.3):
    return SimpleNamespace(brain=SimpleNamespace(validation_run_id=run, temperature=temperature,
                                                api_key="private"))


def test_journal_immutable_identity_and_config(tmp_path):
    journal = DecisionJournal(tmp_path, "p1", "a", config())
    journal.record("decision1", prompt="market", system="control", response='{"new_trades":[]}',
                   reason_code="NO_TRADE_WEAK_EDGE", status="success")
    saved = json.loads((journal.path / "decision1.json").read_text())
    assert saved["experiment_id"] == "p1" and saved["run_id"] == "a"
    with pytest.raises(FileExistsError):
        DecisionJournal(tmp_path, "p1", "a", config())
    # Restart may use new run ID; changing policy requires new experiment ID.
    assert DecisionJournal(tmp_path, "p1", "b", config("b")).config_hash == journal.config_hash
    with pytest.raises(ValueError, match="changed"):
        DecisionJournal(tmp_path, "p1", "c", config("c", temperature=0.9))


@pytest.mark.parametrize("name", ["../escape", "", "..", "/tmp/x"])
def test_journal_rejects_unsafe_identity(tmp_path, name):
    with pytest.raises(ValueError):
        DecisionJournal(tmp_path, name, "run", config())
