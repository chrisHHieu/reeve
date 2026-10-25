"""Phase 0 eval bridge: the memory v2 scenarios as ordinary pytest cases.

Same checks as ``python -m evals.memory.runner``. Pending scenarios (their
roadmap phase has not shipped) are xfails, not failures; provider scenarios skip
unless ``REEVE_EVAL_PROVIDER=1``.
"""

from __future__ import annotations

import pytest

from evals.memory.runner import run_one
from evals.memory.scenarios import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_memory_eval_scenario(scenario, tmp_path):
    result = run_one(scenario, tmp_path)
    if result.status == "FAIL":
        pytest.fail(result.detail)
    if result.status == "SKIP":
        pytest.skip(result.detail)
    if result.status == "PENDING":
        pytest.xfail(f"phase {scenario.phase}: {result.detail}")
