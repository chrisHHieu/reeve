"""The eval ledger: a row names the harness that ran, says whether the run was a
run at all, and carries what its own log shows went wrong."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time

import pytest

from benchmarks import runs


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@local", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    (repo / "harness" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    return repo


def test_a_clean_checkout_is_its_commit_and_uncommitted_harness_work_is_its_own_version(checkout):
    clean = runs.version_of(checkout)
    assert "+" not in clean and len(clean) == 12

    (checkout / "notes.md").write_text("other notes\n", encoding="utf-8")
    assert runs.version_of(checkout) == clean  # not the harness: the same version

    (checkout / "harness" / "engine.py").write_text("x = 2\n", encoding="utf-8")
    edited = runs.version_of(checkout)
    assert edited.startswith(clean + "+")
    (checkout / "harness" / "engine.py").write_text("x = 3\n", encoding="utf-8")
    assert runs.version_of(checkout) not in (clean, edited)  # two edits, two versions

    (checkout / "harness" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    assert runs.version_of(checkout) == clean
    (checkout / "harness" / "new.py").write_text("y = 1\n", encoding="utf-8")
    added = runs.version_of(checkout)
    assert added.startswith(clean + "+")  # a file git does not track yet still changes it
    (checkout / "harness" / "new.py").write_text("y = 2\n", encoding="utf-8")
    assert runs.version_of(checkout) != added


def test_outside_a_checkout_the_version_is_unknown(tmp_path):
    assert runs.version_of(tmp_path) == "unknown"


def test_a_task_digest_follows_what_the_task_is(tmp_path):
    (tmp_path / "a.py").write_text("one", encoding="utf-8")
    first = runs.spec(tmp_path, {"window": 60_000})
    assert runs.spec(tmp_path, {"window": 60_000}) == first
    assert runs.spec(tmp_path, {"window": 0}) != first
    (tmp_path / "a.py").write_text("two", encoding="utf-8")
    assert runs.spec(tmp_path, {"window": 60_000}) != first


def _ran(answers=1, errors=(), fallback=False):
    return {"answers": answers, "reasons": [], "errors": list(errors), "fallback": fallback}


@pytest.mark.parametrize(
    "passed, ran, expected",
    [
        (True, _ran(), ("passed", "")),
        (False, _ran(), ("failed", "")),
        (False, _ran(answers=0), ("void", "no model answer")),
        (False, _ran(errors=["no_quota"]), ("void", "no_quota")),
        (False, _ran(errors=["transport"]), ("void", "transport")),
        (False, _ran(answers=30, errors=["rate_limit"]), ("void", "rate_limit")),
        # The request this harness built and the provider refused is the harness's failure,
        # most of all when it is the first one and no answer ever came (found by running a
        # scripted provider that refuses: the first rule called that run void).
        (False, _ran(errors=["bad_request"]), ("failed", "")),
        (False, _ran(answers=0, errors=["bad_request"]), ("failed", "")),
        (False, _ran(answers=0, errors=["code"]), ("failed", "")),
        (False, _ran(answers=0, errors=["crashed"]), ("failed", "")),
        # An error nobody can name, before any answer: not shown to be the harness's.
        (False, _ran(answers=0, errors=["unknown"]), ("void", "no model answer")),
        (False, _ran(answers=3, errors=["unknown"]), ("failed", "")),
        # The check is the truth: work that passed, then a dead connection, still passed.
        (True, _ran(errors=["transport"]), ("passed", "")),
        # Except where the row would name a model that did not do the work.
        (True, _ran(fallback=True), ("void", "answered on another model")),
        (False, _ran(fallback=True), ("void", "answered on another model")),
    ],
)
def test_a_run_is_passed_failed_or_not_a_run(passed, ran, expected):
    assert runs.outcome(passed, ran) == expected


def _log(state, tag, events):
    folder = state / "conversations"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.time()
    lines = [json.dumps({"seq": i, "t": stamp + i, **event}) for i, event in enumerate(events)]
    (folder / f"{tag}.events.jsonl").write_text("\n".join(lines) + "\n{torn", encoding="utf-8")


def test_how_a_run_went_is_read_from_its_own_log(tmp_path):
    _log(tmp_path, "run", [
        {"type": "turn/start", "turn": 1},
        {"type": "assistant/message", "turn": 1, "step": 1, "usage": {"input": 10}},
        {"type": "assistant/message", "turn": 1, "step": 2},  # a provider that reports no usage
        {"type": "assistant/message", "turn": 1, "step": 3, "partial": True},  # cut mid-answer
        {"type": "llm/fallback", "from": "a", "to": "b"},
        {"type": "turn/end", "turn": 1, "reason": "error", "error": "rate_limit"},
    ])
    assert runs.how_it_ran(tmp_path, "run") == {
        "answers": 2, "reasons": ["error"], "errors": ["rate_limit"], "fallback": True,
    }
    assert runs.how_it_ran(tmp_path, "missing")["answers"] == 0


def test_a_failed_runs_row_carries_what_its_log_shows_and_a_void_one_carries_nothing(tmp_path):
    failed = tmp_path / "failed"
    _log(failed, "t", [
        {"type": "turn/start", "turn": 1},
        {"type": "assistant/message", "turn": 1, "step": 1, "usage": {"input": 10}},
        {"type": "turn/end", "turn": 1, "reason": "error", "error": "bad_request"},
    ])
    row = runs.row(suite="probe", task="p", variant="baseline", model="m", passed=False,
                   state=failed, tag="t", steps=1, tokens=10, task_spec="s", flags=["capped"])
    assert (row["outcome"], row["why"], row["ended"]) == ("failed", "", "error")
    assert row["signs"].get("error-ends") == 1
    assert row["version"] == runs.harness_version() and row["flags"] == ["capped"]

    void = tmp_path / "void"
    _log(void, "t", [
        {"type": "turn/start", "turn": 1},
        {"type": "turn/end", "turn": 1, "reason": "error", "error": "no_key"},
    ])
    row = runs.row(suite="probe", task="p", variant="baseline", model="m", passed=False,
                   state=void, tag="t")
    assert (row["outcome"], row["why"], row["signs"]) == ("void", "no_key", {})


class _Provider:
    """A scripted provider whose every request ends one way."""

    def __new__(cls, ending):
        from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

        class Scripted(ProviderClient):
            def complete(self, *, model, messages, tools=None, **settings):
                if isinstance(ending, Exception):
                    raise ending
                return AssistantTurn(text=ending, finish_reason="stop")

            def capabilities(self, model):
                return ModelCapabilities()

        return Scripted()


@pytest.mark.parametrize(
    "ending, expected",
    [
        ("Done.", ("failed", "")),  # answered, and the work is not done
        (RuntimeError("No API key configured for provider 'x'."), ("void", "no_key")),
        # Refused on the first request, so no answer ever came: still this harness's failure.
        (RuntimeError("Error code: 400 - {'error': {'type': 'invalid_request_error'}}"), ("failed", "")),
    ],
)
def test_the_product_engines_own_log_gives_each_ending_its_outcome(tmp_path, ending, expected):
    import asyncio

    from benchmarks.probe_eval import load_probes, run_probe

    (probe,) = load_probes(["rename-everywhere"])
    result = asyncio.run(run_probe("fake:model", probe, tmp_path, "run", provider=_Provider(ending)))
    row = runs.row(suite="probe", task=probe["id"], variant="baseline", model="fake:model",
                   passed=result["passed"], state=tmp_path, tag="run")
    assert (row["outcome"], row["why"]) == expected


def test_rows_are_appended_one_a_line_and_a_ledger_that_cannot_be_written_costs_nothing(tmp_path, capsys):
    ledger = tmp_path / "evals" / "runs.jsonl"
    runs.record([{"task": "a"}], ledger)
    runs.record([{"task": "b"}, {"task": "c"}], ledger)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"task": "torn')
    assert [row["task"] for row in runs.load(ledger)] == ["a", "b", "c"]
    assert runs.load(tmp_path / "none.jsonl") == []

    runs.record([{"task": "d"}], tmp_path)  # a directory: the write fails, the caller goes on
    assert "not recorded" in capsys.readouterr().out


def test_the_ledger_lives_in_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    assert runs.ledger_path() == tmp_path / "evals" / "runs.jsonl"
