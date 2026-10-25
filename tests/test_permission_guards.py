"""The guard stage (permissions.py): floors are registered, deny-only, and monotonic.

The tool pipeline separates "does the user consent?" (modes, prompts) from
"what must never run regardless of consent?" (guards), and makes the pre-policy stage
monotonic: a guard denies or abstains, never grants, and the verdicts compose by union
of restrictions — so no listener, mode, allowlist or session grant can turn a
refusal into a permission by ordering alone. The floor BEHAVIOUR was already pinned by
test_self_protection.py; what this file pins is the CONTRACT that keeps it true as
guards are added: the composition rule, the order independence, the strengthening-only
direction, and the structural absence of an allow.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from harness.safety.permissions import DEFAULT_GUARDS, GuardVerdict, Mode, PermissionEngine, RiskClass, persistent_authority
from harness.safety.secrets import state_dir


def _engine(tmp_path, mode=Mode.DANGER_FULL_ACCESS, **kw):
    # BYPASS by default: the guard stage must hold in the MOST permissive mode, so any
    # pass here is a real failure, not a mode doing the work.
    return PermissionEngine(workspace_root=tmp_path, mode=mode, **kw)


# -- the composition rule ---------------------------------------------------------


def test_deny_outranks_human_regardless_of_registration_order(tmp_path):
    def asks(engine, name, args, metadata, risk):
        return GuardVerdict.human("a person should see this")

    def refuses(engine, name, args, metadata, risk):
        return GuardVerdict.deny("no")

    human_first = _engine(tmp_path, guards=[asks, refuses])
    deny_first = _engine(tmp_path, guards=[refuses, asks])
    for eng in (human_first, deny_first):
        d = eng.evaluate("custom_tool", {}, None)
        assert not d.allowed and not d.needs_user and d.reason == "no"


def test_reversing_the_default_guards_keeps_every_floor(tmp_path):
    """Registration order is precedence for the reported reason, not for the outcome:
    the union of restrictions is the same backwards."""
    scenarios = [
        ("write_file", {"path": str(state_dir() / "config.toml"), "content": "x"}),
        ("write_file", {"path": ".git/hooks/pre-commit", "content": "x"}),
        ("run_shell", {"command": f'echo x > "{state_dir() / "secrets.json"}"'}),
        ("save_skill", {"name": "s", "content": "x"}),
    ]
    # Below full access, where a human-only floor is still an ask.
    for tool, args in scenarios:
        forward = _engine(tmp_path, Mode.WORKSPACE_WRITE).evaluate(tool, args, None)
        backward = _engine(
            tmp_path, Mode.WORKSPACE_WRITE, guards=list(reversed(DEFAULT_GUARDS))
        ).evaluate(tool, args, None)
        assert not forward.allowed and not backward.allowed, tool
        assert (forward.needs_user, forward.human_only) == (
            backward.needs_user,
            backward.human_only,
        ), tool


def test_guard_order_decides_only_the_reason_when_two_floors_fire(tmp_path):
    """A shell command touching the settings file in plan mode trips self-protection
    AND read-only. Both deny; self-protection is registered first, so its reason is
    the one reported — but either order refuses."""
    args = {"command": f'echo x > "{state_dir() / "config.toml"}"'}
    d = _engine(tmp_path, plan=True).evaluate("run_shell", args, None)
    assert not d.allowed and "Reeve's own settings" in d.reason
    flipped = _engine(
        tmp_path, plan=True, guards=list(reversed(DEFAULT_GUARDS))
    ).evaluate("run_shell", args, None)
    assert not flipped.allowed and "read-only" in flipped.reason


# -- the strengthening-only direction ---------------------------------------------


def test_read_only_plan_strengthens_a_human_verdict_into_a_deny(tmp_path):
    """The one transformation allowed on a guard verdict: a human ask under a read-only
    arrangement becomes an outright deny — plan mode never prompts, so "approval required"
    would be a lie. Never the reverse: nothing turns a refusal into an ask or an ask
    into an allow. (A git-hook write trips BOTH the read-only guard and the deferred-
    execution guard; the deny is what the caller sees.)"""
    d = _engine(tmp_path, plan=True).evaluate(
        "write_file", {"path": ".git/hooks/pre-commit", "content": "x"}, None
    )
    assert not d.allowed and not d.needs_user and "read-only" in d.reason


def test_a_metadata_slip_cannot_switch_persistent_authority_off(tmp_path):
    """The floor is keyed on the tool NAME alone. Whatever the classifier is told —
    a bad metadata record, a user override, a future refactor — the verdict stands."""
    eng = _engine(tmp_path, Mode.DANGER_FULL_ACCESS)
    for risk in RiskClass:
        verdict = persistent_authority(eng, "save_skill", {}, None, risk)
        assert verdict is not None and verdict.kind == "human"


# -- the structural absence of an allow -------------------------------------------


def test_a_guard_verdict_has_no_allow_to_give():
    """The contract's teeth: `GuardVerdict` cannot express a grant, so a guard that
    wants to allow can only abstain — and abstaining hands the decision to the mode
    and allowlists, where every existing rule still applies. (`axis` names which of
    the two questions — boundary reach or consent — the verdict answered; it is still
    not an allow.)"""
    assert not hasattr(GuardVerdict, "allow")
    fields = {f for f in GuardVerdict.__dataclass_fields__}
    assert fields == {"kind", "reason", "axis"}


def test_an_abstaining_guard_leaves_the_decision_to_the_policy(tmp_path):
    def silent(engine, name, args, metadata, risk):
        return None

    eng = _engine(tmp_path, Mode.DANGER_FULL_ACCESS, guards=[silent])
    assert eng.evaluate("write_file", {"path": "src/app.py", "content": "x"}, None).allowed


def test_a_later_registered_guard_tightens_bypass_mode(tmp_path):
    """Guards added after the fact can only ever add restrictions — the exact property
    that makes it safe to let future code register one."""

    def refuses_notes(engine, name, args, metadata, risk):
        if name == "write_file" and str(args.get("path", "")).endswith(".md"):
            return GuardVerdict.deny("no markdown today")
        return None

    eng = _engine(tmp_path, Mode.DANGER_FULL_ACCESS, guards=[*DEFAULT_GUARDS, refuses_notes])
    assert not eng.evaluate("write_file", {"path": "docs/x.md", "content": "y"}, None).allowed
    assert eng.evaluate("write_file", {"path": "docs/x.py", "content": "y"}, None).allowed


# -- the stage holds under every auto-allow path ----------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"auto_allow_tools": {"write_file", "save_skill", "run_shell"}},
        {"allowed_commands": ["echo"]},
    ],
)
def test_floors_win_over_every_grant_combination(tmp_path, kwargs):
    # Every grant below full access; full access is the person's answer given in advance
    # (the test after this one).
    eng = _engine(tmp_path, Mode.WORKSPACE_WRITE, **kwargs)
    eng.allow_tool_for_session("write_file")
    eng.allow_tool_for_session("save_skill")
    eng.allow_command_for_session("echo")
    eng.task_rules["save_skill"] = {"whatever"}

    cases = [
        ("write_file", {"path": str(state_dir() / "config.toml"), "content": "x"}, False),
        ("run_shell", {"command": f'echo x > "{state_dir() / "secrets.json"}"'}, False),
        ("write_file", {"path": ".github/workflows/ci.yml", "content": "x"}, True),
        ("save_skill", {"name": "s"}, True),
    ]
    for tool, args, human in cases:
        d = eng.evaluate(tool, args, None)
        assert not d.allowed, f"{tool} was granted over a floor by {kwargs}"
        assert d.needs_user is human and d.human_only is human, tool


def test_full_access_clears_every_ask_and_keeps_every_refusal(tmp_path):
    """Full access asks nobody. A human-only floor (a file that runs later,
    authority that outlives the session) runs there; a deny (Reeve's own settings)
    still refuses, and the plan overlay still refuses everything consequential."""
    eng = _engine(tmp_path)
    for tool, args in [
        ("write_file", {"path": ".github/workflows/ci.yml", "content": "x"}),
        ("write_file", {"path": ".git/hooks/pre-commit", "content": "x"}),
        ("save_skill", {"name": "s", "content": "x"}),
    ]:
        d = eng.evaluate(tool, args, None)
        assert d.allowed and not d.needs_user, tool
    for tool, args in [
        ("write_file", {"path": str(state_dir() / "config.toml"), "content": "x"}),
        ("run_shell", {"command": f'echo x > "{state_dir() / "secrets.json"}"'}),
    ]:
        d = eng.evaluate(tool, args, None)
        assert not d.allowed and not d.needs_user, tool
    planned = _engine(tmp_path, plan=True)
    d = planned.evaluate("save_skill", {"name": "s", "content": "x"}, None)
    assert not d.allowed


def test_an_out_of_root_write_is_a_grantable_ask_not_a_floor(tmp_path):
    """Out-of-root writes follow the path policy — the preset and the
    person's grants decide, they are NOT a guard (a guard would make them unapprovable
    again). The ask names the folder so the card can offer the remedy."""
    outside = {"path": "../outside.txt", "content": "x"}

    asking = _engine(tmp_path, Mode.WORKSPACE_WRITE).evaluate("write_file", outside, None)
    assert asking.needs_user and not asking.human_only
    assert "not in a writable directory" in asking.reason and "outside.txt" in asking.reason

    # Every ordinary grant path clears it, exactly as it clears any other ask.
    granted = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    granted.allow_tool_for_session("write_file")
    assert granted.evaluate("write_file", outside, None).allowed
    auto = _engine(tmp_path, Mode.WORKSPACE_WRITE, auto_allow_tools={"write_file"})
    assert auto.evaluate("write_file", outside, None).allowed
    assert _engine(tmp_path).evaluate("write_file", outside, None).allowed  # full access

    # Read-only still refuses outright: no prompt to answer.
    refused = _engine(tmp_path, Mode.READ_ONLY).evaluate("write_file", outside, None)
    assert not refused.allowed and not refused.needs_user


def test_replace_keeps_the_default_stage(tmp_path):
    """`dataclasses.replace` (used by callers adjusting one knob) carries the stage
    over intact — by reference, the same deliberate sharing `roots` uses."""
    eng = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    adjusted = replace(eng, mode=Mode.DANGER_FULL_ACCESS)
    assert adjusted.guards == eng.guards == list(DEFAULT_GUARDS)
