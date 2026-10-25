"""The session-log vocabulary cannot drift (scripts/verify_session_log_vocab.py).

Pins the two-way check into the suite: every event the code writes is documented in
`harness/session/session_log.py`'s table, and every documented type is really written. The
first run of the verifier found six undocumented event types and one fiction
(compaction events the code never wrote) — this test is what keeps it true.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_session_log_vocab import verify  # noqa: E402


def test_the_vocabulary_table_matches_what_the_code_writes():
    problems = verify()
    assert problems == [], "\n".join(problems)
