"""The end-to-end smoke script's plumbing, run offline (no provider call).

`scripts/e2e_harness_smoke.py --offline` covers the deterministic checks — attachment
spill, image fitting, syntax diagnostics — through the real engine and the real spill
store. The live checks (manual compaction, goal rounds) spend tokens and are run by hand;
this test keeps the script itself from rotting.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "e2e_harness_smoke.py"


def test_the_offline_smoke_passes(tmp_path):
    env = {**os.environ, "REEVE_STATE_DIR": str(tmp_path / "state")}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--offline"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("PASS") >= 3
    assert "FAIL" not in proc.stdout
    assert "3/3 checks passed" in proc.stdout
