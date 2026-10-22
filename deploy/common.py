"""Shared settings and a thin wrapper over the Databricks CLI (so the deploy scripts need no Python deps)."""
from __future__ import annotations

import json
import os
import subprocess
import uuid

PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
CATALOG = os.environ["LRC_CATALOG"]
SCHEMA = os.environ.get("LRC_SCHEMA", "loss_ratio_coworker")
WAREHOUSE_ID = os.environ["LRC_WAREHOUSE_ID"]
PARENT_PATH = os.environ.get("LRC_PARENT_PATH")  # e.g. /Workspace/Users/<you>/genie-loss-ratio-coworker

AGENT_TITLE = "Loss Ratio Coworker"
DASHBOARD_TITLE = "Daily Loss Ratio Watch"


def fq(name: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{name}"


def cli(*args: str, body: dict | None = None) -> dict:
    cmd = ["databricks", *args, "--profile", PROFILE, "-o", "json"]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"{' '.join(args[:4])} failed:\n{res.stderr[:2000]}")
    return json.loads(res.stdout or "{}")


def new_id() -> str:
    """Genie and Lakeview object ids are 32-char lowercase hex."""
    return uuid.uuid4().hex
