"""Item 4.1 — a receipt for the turn, from what the engine SAW.

At the end of a turn the person is handed a paragraph the model wrote about its own work.
"I converted all 200 files" is a claim, not a receipt, and someone who does not write
software has no way to check it. So the load-bearing test here is not "the summary lists a
file" — it is that the receipt stays right while the model's prose is WRONG, because that
is the only case where a receipt is worth anything.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from harness.session.changes import ChangeLog, change_listener
from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine, write_paths
from harness.safety.provenance import resolve as resolve_path
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.risk import RiskClass, classify
from harness.tools import ToolRegistry


@dataclass
class _Meta:
    category: str = ""
    risk_level: str = "high"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _registry(root: Path, broken: set[str]) -> ToolRegistry:
    def write_file(path: str, content: str) -> str:
        """Write a file.

        Args:
            path: where
            content: what
        """
        if path in broken:
            raise RuntimeError("disk said no")
        # Relative paths hang off the workspace, exactly as the real file tools resolve
        # them (`root=ws`). Writing `Path(path)` straight out would land in the TEST
        # PROCESS's cwd — which is the repository, and which is how this suite left a
        # stray notes.txt in it once.
        target = Path(path)
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return "written"

    def send_message(target: str, text: str) -> str:
        """Send a message.

        Args:
            target: where
            text: what
        """
        return "sent"

    def read_file(path: str) -> str:
        """Read a file.

        Args:
            path: which
        """
        return "contents"

    registry = ToolRegistry()
    registry.register(write_file, metadata=_Meta())
    registry.register(send_message, metadata=_Meta(category="connector", requires_approval=True))
    registry.register(read_file, metadata=_Meta(risk_level="low"))
    return registry


def _engine(tmp_path: Path, turns: list[AssistantTurn], *, broken: Optional[set] = None):
    registry = _registry(tmp_path, broken or set())
    permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS)
    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=permissions,
        model="m",
    )

    def _describe(name: str, arguments: dict) -> str:
        return f"{name}->{arguments.get('target', '')}"

    def _consequential(name: str) -> bool:
        spec = registry.get(name)
        return classify(name, spec.metadata if spec else None, None) is not RiskClass.READ

    engine.changes = ChangeLog(
        locate=write_paths,
        describe=_describe,
        consequential=_consequential,
        resolve=lambda raw: resolve_path(raw, tmp_path),
    )
    engine.pipeline.on_pre_execute(change_listener(engine.changes))
    return engine


def _calls(*specs: tuple[str, str, dict]) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=cid, name=n, arguments=a) for cid, n, a in specs],
        finish_reason="tool_calls",
    )


def _run(engine: TurnEngine, text: str = "go") -> list[Any]:
    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def _receipt(events: list[Any]) -> dict:
    for event in events:
        if event.type in (EventType.TURN_END, EventType.INTERRUPTED, EventType.ERROR):
            return event.data.get("changes", {})
    return {}


def _write(cid: str, path: Path, content: str = "x") -> tuple[str, str, dict]:
    return (cid, "write_file", {"path": str(path), "content": content})


# -- the one that matters ----------------------------------------------------------


def test_the_receipt_is_right_when_the_prose_is_wrong(tmp_path):
    """The whole point. The model claims it converted 200 files; it wrote one. A person
    reading the answer cannot tell. The receipt is not derived from the answer, so it
    can't inherit the lie."""
    a = tmp_path / "one.txt"
    engine = _engine(
        tmp_path,
        [
            _calls(_write("c1", a)),
            AssistantTurn(text="Converted all 200 files.", finish_reason="stop"),
        ],
    )
    events = _run(engine)

    said = [e for e in events if e.type == EventType.ASSISTANT_MESSAGE][-1].data["text"]
    assert "200" in said  # the claim is on the record
    receipt = _receipt(events)
    assert receipt["created"] == [str(a)]  # the receipt is not the prose
    assert receipt["stats"][0]["added"] > 0  # and now it counts the lines too


def test_created_and_overwritten_are_not_the_same_word(tmp_path):
    """The half a person actually reacts to. Overwriting a file that was already theirs
    is the frightening one; "wrote 2 files" hides it."""
    mine = tmp_path / "mine.txt"
    mine.write_text("my work", encoding="utf-8")
    fresh = tmp_path / "fresh.txt"

    engine = _engine(tmp_path, [_calls(_write("c1", mine), _write("c2", fresh))])
    receipt = _receipt(_run(engine))

    assert receipt["overwritten"] == [str(mine)]
    assert receipt["created"] == [str(fresh)]


# -- only what happened ------------------------------------------------------------


def test_a_failed_write_is_not_on_the_receipt(tmp_path):
    """Recording is split across the listener and the result for exactly this: the call
    was proposed, the listener noted it, and then it changed nothing."""
    ok, bad = tmp_path / "ok.txt", tmp_path / "bad.txt"
    engine = _engine(tmp_path, [_calls(_write("c1", ok), _write("c2", bad))],
                     broken={str(bad)})
    receipt = _receipt(_run(engine))

    assert receipt["created"] == [str(ok)]
    assert not bad.exists()


def test_a_turn_that_only_read_leaves_no_receipt(tmp_path):
    """Absent, not empty. A receipt after every answered question is noise that trains
    people to stop reading it."""
    engine = _engine(tmp_path, [_calls(("c1", "read_file", {"path": "a.txt"}))])
    assert _receipt(_run(engine)) == {}


def test_a_send_is_named_in_the_tool_s_own_words(tmp_path):
    engine = _engine(
        tmp_path,
        [_calls(("c1", "send_message", {"target": "slack:C1", "text": "hi"}))],
    )
    assert _receipt(_run(engine))["did"] == ["send_message->slack:C1"]


def test_writing_the_same_new_file_twice_is_still_one_creation(tmp_path):
    """The second write sees the file the FIRST one made. Reporting it as an overwrite
    would tell the person their file was replaced, when it never was theirs."""
    path = tmp_path / "draft.md"
    engine = _engine(tmp_path, [_calls(_write("c1", path, "v1"), _write("c2", path, "v2"))])
    receipt = _receipt(_run(engine))

    assert receipt["created"] == [str(path)]
    assert "overwritten" not in receipt


# -- the turn's edges --------------------------------------------------------------


def test_the_receipt_survives_a_turn_that_failed(tmp_path):
    """Nine files written and THEN a crash is the moment someone most needs the list."""
    path = tmp_path / "half.txt"

    class _Dies(_Scripted):
        def complete(self, *, model, messages, tools=None, **settings):
            if self._turns:
                return self._turns.pop(0)
            raise RuntimeError("the model connection died")

    engine = _engine(tmp_path, [])
    engine.provider = _Dies([_calls(_write("c1", path))])
    events = _run(engine)

    assert [e for e in events if e.type == EventType.ERROR]
    receipt = _receipt(events)
    assert receipt["created"] == [str(path)]
    assert receipt["stats"][0]["added"] > 0  # the crash does not cost the receipt its numbers


def test_each_turn_gets_its_own_receipt(tmp_path):
    """It is a receipt for a TURN, not a running total — the second turn must not
    re-report the first turn's files as though it had just written them."""
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    engine = _engine(
        tmp_path,
        [_calls(_write("c1", first)), AssistantTurn(text="ok", finish_reason="stop")],
    )
    assert _receipt(_run(engine))["created"] == [str(first)]

    engine.provider = _Scripted(
        [_calls(_write("c2", second)), AssistantTurn(text="ok", finish_reason="stop")]
    )
    assert _receipt(_run(engine, "again"))["created"] == [str(second)]


# -- it outlives the live view ----------------------------------------------------


def test_the_receipt_is_in_the_transcript_and_never_reaches_the_model(tmp_path):
    """A reopened session showed no receipt at all (GUI review): the list rode only the
    live turn-end frame. It is now persisted as a display-only notice, which the outbound
    view drops like every other notice."""
    path = tmp_path / "kept.txt"
    seen: list[list[dict]] = []

    class _Recording(_Scripted):
        def complete(self, *, model, messages, tools=None, **settings):
            seen.append(list(messages))
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    engine = _engine(tmp_path, [])
    engine.provider = _Recording(
        [_calls(_write("c1", path)), AssistantTurn(text="ok", finish_reason="stop")]
    )
    _run(engine)

    [notice] = [m for m in engine.messages if m.get("role") == "notice"]
    assert notice["kind"] == "changes"
    assert notice["changes"]["created"] == [str(path)]
    assert notice["changes"]["stats"][0]["added"] > 0

    engine.provider = _Recording([AssistantTurn(text="still here", finish_reason="stop")])
    _run(engine, "and now?")
    assert all(m.get("role") != "notice" and "changes" not in m for m in seen[-1])


def test_a_turn_that_changed_nothing_leaves_no_receipt_in_the_transcript(tmp_path):
    engine = _engine(tmp_path, [AssistantTurn(text="Just answering.", finish_reason="stop")])
    _run(engine)
    assert not [m for m in engine.messages if m.get("role") == "notice"]


def test_a_failed_turn_s_receipt_does_not_cost_the_retry(tmp_path):
    """The receipt lands after the error notice, and `retry` only runs when the tail is
    an error — a receipt read as the tail would have taken the Retry away."""
    path = tmp_path / "half.txt"

    class _Dies(_Scripted):
        def complete(self, *, model, messages, tools=None, **settings):
            if self._turns:
                return self._turns.pop(0)
            raise RuntimeError("the model connection died")

    engine = _engine(tmp_path, [])
    engine.provider = _Dies([_calls(_write("c1", path))])
    _run(engine)

    assert [m.get("kind") for m in engine.messages if m.get("role") == "notice"] == [
        "error",
        "changes",
    ]
    assert engine._tail_is_retriable_error()


# -- it is a receipt, not a gate ---------------------------------------------------


def test_a_broken_receipt_never_blocks_the_work(tmp_path):
    """Same policy as undo's snapshot listener and the OPPOSITE of intents': the
    pre-execute stage fails closed, and this listener must not inherit that. A receipt
    that cannot be written is no reason to refuse a write."""
    path = tmp_path / "kept.txt"

    class _Broken(ChangeLog):
        def note(self, call_id, tool_name, arguments):
            raise OSError("no space left on device")

    engine = _engine(tmp_path, [_calls(_write("c1", path))])
    engine.changes = _Broken(
        locate=write_paths, describe=lambda n, a: n, consequential=lambda n: True
    )
    engine.pipeline.on_pre_execute(change_listener(engine.changes))
    events = _run(engine)

    assert path.read_text(encoding="utf-8") == "x"  # the work happened
    assert _receipt(events) == {}  # only the receipt was lost


# -- the case that actually happens ------------------------------------------------


def test_a_relative_path_is_read_against_the_workspace(tmp_path):
    """A model writes `notes.txt`, never an absolute path — so this IS the case, not an
    edge one. Before the resolver, `Path("notes.txt").exists()` asked about this
    PROCESS's cwd and answered "created" about a file it had just overwritten. A receipt
    that is confidently wrong is worse than no receipt, which is the whole point of it.
    Every test above passes absolute paths, which is exactly why they all missed this.
    """
    (tmp_path / "notes.txt").write_text("theirs", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [_calls(("c1", "write_file", {"path": "notes.txt", "content": "mine"}))],
    )
    receipt = _receipt(_run(engine))

    assert receipt.get("overwritten") == [str(tmp_path / "notes.txt")]
    assert "created" not in receipt


def test_a_region_says_where_its_window_starts_in_the_file():
    # GUI tools review, B2: a hunk is a window with three lines of context, and without
    # its start a card numbered line 7 of the file as line 4.
    from harness.tools.file_diff import _regions

    before = "".join(f"line {i}\n" for i in range(1, 11))
    after = before.replace("line 7\n", "line seven\n")
    (region,) = _regions(before, after)
    assert (region.old_start, region.new_start) == (4, 4)
    assert region.new.splitlines()[3] == "line seven"  # 4 + 3 = the file's line 7
    (created,) = _regions(None, "x\n")
    assert (created.old_start, created.new_start) == (None, 1)


def _with_real_write_contract(engine: TurnEngine) -> TurnEngine:
    from harness.tools.files.contracts import BORROWED_CONTRACTS as _BORROWED_CONTRACTS

    engine.registry.get("write_file").contract = _BORROWED_CONTRACTS["write_file"]
    return engine


def _cards(events: list[Any], kind: EventType) -> list[list[dict]]:
    return [
        event.data["presentation"]["card"]["diffs"]
        for event in events
        if event.type is kind and (event.data.get("presentation") or {}).get("card")
    ]


def test_an_overwrite_proposes_the_diff_it_will_make(tmp_path):
    # GUI UX audit U3: `write_file`'s call card claimed the whole file as added, so a
    # one-line overwrite of a ten-line file showed ten green lines and then shrank to the
    # real diff once it ran. The proposal now carries the hunks the result will.
    target = tmp_path / "notes.txt"
    before = "".join(f"line {i}\n" for i in range(1, 11))
    target.write_text(before, encoding="utf-8")
    engine = _with_real_write_contract(
        _engine(tmp_path, [_calls(_write("c1", target, before.replace("line 7\n", "seven\n")))])
    )
    events = _run(engine)

    (proposed,) = _cards(events, EventType.TOOL_PROPOSED)
    (finished,) = _cards(events, EventType.TOOL_FINISHED)
    assert proposed == finished
    assert (proposed[0]["added"], proposed[0]["removed"]) == (1, 1)
    assert proposed[0]["old_text"] is not None


def test_a_new_file_keeps_the_tool_s_own_call_card(tmp_path):
    # Nothing to read before a creation: the all-added card is already the truth.
    target = tmp_path / "fresh.txt"
    engine = _with_real_write_contract(_engine(tmp_path, [_calls(_write("c1", target, "a\nb\n"))]))
    (proposed,) = _cards(_run(engine), EventType.TOOL_PROPOSED)
    assert proposed == [{"path": str(target), "old_text": None, "new_text": "a\nb\n"}]
