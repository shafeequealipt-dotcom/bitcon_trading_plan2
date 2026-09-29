"""Optional immutable Call-A records for experiments; no exchange/provider calls."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, is_dataclass
from pathlib import Path

SECRET = re.compile(
    r"secret|password|credential|api_key|access_token|auth_token|private_key|(?:^|_)token$", re.I
)


def canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def redact(value):
    """Exclude credentials from config snapshots, including nested tables/lists."""
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if SECRET.search(k) or k.lower() in {"token", "authorization", "cookie", "chat_id"}
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and (
        re.search(r"://[^/]*@", value) or re.search(r"[?&](token|key|signature)=", value, re.I)
    ):
        return "[REDACTED_URL]"
    return value


def snapshot_settings(settings):
    def convert(value):
        if is_dataclass(value):
            return asdict(value)
        if hasattr(value, "__dict__"):
            return {k: convert(v) for k, v in vars(value).items() if not k.startswith("_")}
        return value

    return redact(convert(settings))


class DecisionJournal:
    """One writer per run. A changed config requires a new experiment ID.

    Files contain private account/market context. Keep them out of git.
    Config drift is rejected before inference; archived responses permit
    deterministic parser replay, not deterministic model reruns.
    """

    def __init__(self, root, experiment_id, run_id, config):
        for value in (experiment_id, run_id):
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value)
                or value in {".", ".."}
            ):
                raise ValueError("Use nonempty safe experiment_id and run_id")
        self.experiment_id, self.run_id = experiment_id, run_id
        self.config = snapshot_settings(config)
        stable = json.loads(canonical(self.config))
        for key in ("validation_run_id", "validation_audit_dir"):
            stable.get("brain", {}).pop(key, None)
        try:
            revision = subprocess.check_output(
                ["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
                text=True,
                timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            raise ValueError("An experiment journal requires a versioned source checkout")
        self.revision = revision
        stable["source_commit"] = revision
        self.config_hash = digest(stable)
        experiment = Path(root) / experiment_id
        experiment.mkdir(parents=True, exist_ok=True)
        identity = experiment / "experiment.json"
        content = {"config_sha256": self.config_hash, "config": stable}
        try:
            with identity.open("x") as f:
                f.write(canonical(content) + "\n")
        except FileExistsError:
            if json.loads(identity.read_text()) != content:
                raise ValueError("Experiment configuration changed; use a new experiment_id")
        self.path = experiment / run_id
        self.path.mkdir(exist_ok=False)

    def assert_config(self, settings):
        stable = snapshot_settings(settings)
        for key in ("validation_run_id", "validation_audit_dir"):
            stable.get("brain", {}).pop(key, None)
        stable["source_commit"] = self.revision
        if digest(stable) != self.config_hash:
            raise ValueError("Experiment configuration changed during run")
        brain = snapshot_settings(settings).get("brain", {})
        if brain.get("validation_run_id") != self.run_id:
            raise ValueError("Experiment run ID changed; restart with a new run")

    def record(self, decision_id, *, prompt, system, response, reason_code, status):
        # Internal decision IDs are UUID-like; validate before using as a path.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", decision_id) or decision_id in {".", ".."}:
            raise ValueError("Invalid decision ID")
        value = {
            "format_version": 1,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "decision_id": decision_id,
            "config_sha256": self.config_hash,
            "opportunity_id": hashlib.sha256(prompt.encode()).hexdigest(),
            "system_sha256": hashlib.sha256(system.encode()).hexdigest(),
            "prompt": prompt,
            "system": system,
            "response": response,
            "reason_code": reason_code,
            "status": status,
        }
        with (self.path / f"{decision_id}.json").open("x") as f:
            f.write(canonical(value) + "\n")
