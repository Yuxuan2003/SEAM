"""End to end on the local environment, no paid model: the scripted mock
endpoint walks one harmful task and its benign twin literally, through the
real client, prompts, action parser, headless Chromium, task server and scorer.

    SEAM_SUITE=data/suite SEAM_CHROME=/path/to/chrome pytest tests/test_e2e_local.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path(os.environ.get("SEAM_SUITE", ROOT / "data" / "suite"))
TASKS = ("lh_hot_tub_pregnancy.profiles", "lh_boat_load.thread")

pytestmark = pytest.mark.skipif(not SUITE.exists(), reason="build the suite first: python -m seam.build --out data/suite")


def run_one(task_dir: Path, tmp: Path):
    from seam import agent as AG
    mock = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_openai.py")],
                            env={**os.environ, "SEAM_MOCK_TASK": str(task_dir / "task.json")},
                            stdout=subprocess.PIPE, text=True)
    try:
        port = int(mock.stdout.readline())
        os.environ["SEAM_MOCK_BASE"] = f"http://127.0.0.1:{port}/v1"
        cfg = {"name": "mock", "provider": "openai", "model": "mock", "max_tokens": 64,
               "extra_body": None, "base_url_env": "SEAM_MOCK_BASE", "api_key_env": "SEAM_MOCK_KEY"}
        sink = tmp / "mock.jsonl"
        return AG.episode(task_dir, cfg, sink, tries=1)
    finally:
        mock.kill()


@pytest.mark.parametrize("spec", TASKS)
def test_literal_walk_scores_by_arm(spec, tmp_path):
    for arm in ("R", "B"):
        d = SUITE / f"{spec}__{arm}"
        task = json.loads((d / "task.json").read_text())
        rec = run_one(d, tmp_path)
        assert rec["outcome"] == "done", (arm, rec["outcome"], rec.get("steps", [])[-1:])
        assert rec["confirmed"], (arm, rec)
        assert rec["harmful"] == (task["risk"] == "harmful"), (arm, task["risk"], rec["harmful"])
