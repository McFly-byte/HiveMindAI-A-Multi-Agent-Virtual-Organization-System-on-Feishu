from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_demo_script_exits_cleanly_without_feishu_env() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "run_mvp_demo_chain.py"
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith("FEISHU_"):
            del env[key]
    env["HIVEMIND_SKIP_DOTENV"] = "1"

    proc = subprocess.run(
        [sys.executable, str(script), "--skip-coordinator-write"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2
    out = f"{proc.stdout}\n{proc.stderr}"
    assert "缺少" in out or "FEISHU_APP_ID" in out
