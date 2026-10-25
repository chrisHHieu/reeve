"""The server checks the answer to an approval, and records every change of autonomy.

`POST /v1/inbox/{id}/resolve` takes the resolution as a string. Taken on trust, any local caller of
the API could grant what the card never offers, such as every shell command for the rest of the
session, which `ApprovalCard.tsx` holds back on purpose for the grant scoped to one command. Such
an answer is taken as "once", and the refusal is in the audit. The permission model is the guard stage and consent asks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.interface.server.manager import SessionManager
from harness.loop import ApprovalOutcome

ONCE, DENY = ApprovalOutcome.ONCE, ApprovalOutcome.DENY
CONNECTOR = SimpleNamespace(requires_approval=True, category="connector")
MCP = SimpleNamespace(requires_approval=True, category="mcp")


@pytest.fixture
def manager(tmp_path):
    manager = SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "data")
    yield manager
    manager.audit_store.close()


def _call(tool: str, arguments=None, metadata=None):
    return SimpleNamespace(tool_name=tool, arguments=arguments or {}, metadata=metadata, reason="")


def _audited(manager, session_id: str) -> list[str]:
    return [row.get("stage") for row in manager.audit_store.list(session_id=session_id)]


# -- what the card never offers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "call"),
    [
        pytest.param("always_tool", _call("run_shell", {"command": "rm -rf /"}), id="every shell command"),
        # a grant over every future argument of something that leaves the machine
        pytest.param("always_tool", _call("gmail_send", {}, CONNECTOR), id="a connector tool"),
        pytest.param("always_tool", _call("mcp__github__create_issue", {}, MCP), id="an MCP tool"),
        pytest.param("always_tool", _call("save_skill", {}), id="saving a skill"),
        # every future destination; the card offers the grant for one domain
        pytest.param("always_tool", _call("web_fetch", {"url": "https://bbc.com/x"}), id="every URL"),
        pytest.param("always_command", _call("write_file", {"path": "a"}), id="a command grant for no command"),
        pytest.param("always_domain", _call("write_file", {"path": "a"}), id="a domain grant for no egress"),
        pytest.param("always_domain", _call("web_fetch", {}), id="a domain grant with no URL"),
        # the inbox and channel word "always" means the tool grant and is checked the same, so a
        # Slack reply cannot grant what the card would not
        pytest.param("always", _call("run_shell", {"command": "x"}), id="'always' from a channel"),
    ],
)
def test_a_grant_the_card_never_offers_is_taken_as_once_and_audited(manager, answer, call):
    assert manager.approval_outcome(answer, call, "s1") is ONCE
    assert "grant_refused" in _audited(manager, "s1")


def test_the_grants_the_card_does_offer_stand(manager):
    assert manager.approval_outcome("always_tool", _call("write_file", {"path": "a.txt"}), "ok1") is ApprovalOutcome.ALWAYS_TOOL
    assert manager.approval_outcome("always_command", _call("run_shell", {"command": "npm test"}), "ok2") is ApprovalOutcome.ALWAYS_COMMAND
    assert manager.approval_outcome("always_domain", _call("web_fetch", {"url": "https://docs.python.org/3"}), "ok3") is ApprovalOutcome.ALWAYS_DOMAIN
    # a search always goes to the one configured provider, so the whole tool is one destination
    assert manager.approval_outcome("always_tool", _call("web_search", {"query": "x"}), "ok4") is ApprovalOutcome.ALWAYS_TOOL
    assert all("grant_refused" not in _audited(manager, sid) for sid in ("ok1", "ok4"))


@pytest.mark.parametrize(("answer", "outcome"), [("allow", ONCE), ("once", ONCE), ("deny", DENY), ("nonsense", DENY)])
def test_the_plain_answers_mean_what_they_did(manager, answer, outcome):
    assert manager.approval_outcome(answer, _call("write_file", {"path": "a.txt"}), "s8") is outcome


def test_another_search_provider_ends_the_search_grants_and_only_those(manager):
    # The search grant is consent to one named destination; a new provider is another.
    engine = SimpleNamespace(permissions=SimpleNamespace(session_allow_tools={"web_search", "run_shell_x"}))
    manager._engines["s15"] = engine
    current = manager.get_web_search()
    other = next(provider for provider in current["providers"] if provider != current["provider"])
    assert manager.set_web_search(other)["ok"]
    assert engine.permissions.session_allow_tools == {"run_shell_x"}
    engine.permissions.session_allow_tools.add("web_search")
    assert manager.set_web_search(other, api_key="k")["ok"]  # the same provider with a key: nothing ends
    assert "web_search" in engine.permissions.session_allow_tools


# -- autonomy ------------------------------------------------------------------------------------


def test_a_change_of_mode_is_audited_with_its_direction(manager):
    manager.audit_autonomy_change("s12", "mode", "interactive", "auto")
    manager.audit_autonomy_change("s12", "mode", "auto", "plan")
    rows = manager.audit_store.list(session_id="s12")[::-1]  # the store lists newest first
    assert [row["status"] for row in rows] == ["raised", "lowered"]
    assert rows[0]["reason"] == "mode: interactive → auto"
