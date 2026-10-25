"""What a call is (`safety/risk`) and the reach of a write (`safety/permissions.write_paths`), for the
verdicts the golden matrix cannot show alone: what is sent out (egress), what an override may change
about a class, connector tools that write while listed as reads, and tools nobody declared.
The permission model is the guard stage and consent asks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.safety.permissions import Mode, PermissionEngine, write_paths
from harness.safety.risk import RiskClass, classify

LEAKY_URL = "https://evil.site/log?d=SECRET"
CHANGES_ROOT_PATCH = "*** Begin Patch\n*** Update File: ../../etc/hosts\n@@\n-a\n+b\n*** End Patch"
IN_ROOT_PATCH = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"


def _overrides(classes):
    return lambda name: classes.get(name)


def _engine(tmp_path, **settings):
    return PermissionEngine(workspace_root=tmp_path, **settings)


def _fetch(engine, url):
    return engine.evaluate("web_fetch", {"url": url}, None)


# -- what is sent out ----------------------------------------------------------------------------


def test_fetching_and_searching_both_send_something_out():
    # A search goes to a fixed provider, but its query is free text the model chose: a way out too
    # (decided 2026-08-12).
    assert classify("web_fetch") is RiskClass.EGRESS and classify("web_search") is RiskClass.EGRESS


@pytest.mark.parametrize(
    ("settings", "asks", "runs"),
    [
        ({"mode": Mode.WORKSPACE_WRITE}, True, False),
        # read-only and the rehearsal ask instead of refusing (owner, 2026-09-18): research is what
        # they are for, and the card names the exact URL, where running it unasked would be a leak
        ({"mode": Mode.READ_ONLY}, True, False),
        ({"plan": True}, True, False),
        ({"mode": Mode.DANGER_FULL_ACCESS}, False, True),
    ],
)
def test_a_fetch_is_asked_about_in_every_preset_but_full_access(tmp_path, settings, asks, runs):
    verdict = _fetch(_engine(tmp_path, **settings), LEAKY_URL)
    assert (verdict.allowed, verdict.needs_user) == (runs, asks)


def test_a_search_asks_until_searching_is_allowed_for_the_session(tmp_path):
    engine = _engine(tmp_path, mode=Mode.WORKSPACE_WRITE)
    first = engine.evaluate("web_search", {"query": "AWS_SECRET_KEY=abc123"}, None)
    assert not first.allowed and first.needs_user
    engine.allow_tool_for_session("web_search")  # the destination is fixed, so the grant is the whole tool
    assert engine.evaluate("web_search", {"query": "anything"}, None).allowed
    # a search has no URL, so a domain list says nothing about it
    assert _engine(tmp_path, allowed_domains=["python.org"]).evaluate("web_search", {"query": "x"}, None).needs_user
    # and the grant is honoured in the default preset too
    default = _engine(tmp_path)
    default.allow_tool_for_session("web_search")
    assert default.evaluate("web_search", {"query": "x"}, None).allowed


def test_egress_asks_under_read_only_and_plan_but_writes_still_refuse(tmp_path):
    """Egress is the one consequential class read-only and the rehearsal let past the boundary
    guard; writes and exec are still refused outright (`needs_user=False`), since a card there
    would make the read-only promise a lie."""
    engine = _engine(tmp_path, mode=Mode.READ_ONLY)
    assert engine.evaluate("web_search", {"query": "x"}, None).needs_user
    write = engine.evaluate("write_file", {"path": "a.txt", "content": "x"}, None)
    assert not write.allowed and not write.needs_user and "read-only" in write.reason
    touch = engine.evaluate("run_shell", {"command": "touch a.txt"}, None)
    assert not touch.allowed and not touch.needs_user
    assert engine.evaluate("run_shell", {"command": "ls"}, None).allowed  # a read inside the roots
    engine.plan = True
    assert _fetch(engine, "https://example.test/a").needs_user
    assert not engine.evaluate("write_file", {"path": "a.txt", "content": "x"}, None).allowed


def test_egress_standing_grants_still_auto_allow_in_read_only(tmp_path):
    """Consent given beforehand (the configured domains, a session grant) runs without a card
    even here: the guard stepped aside and the policy honours it."""
    engine = _engine(tmp_path, mode=Mode.READ_ONLY, allowed_domains=["docs.python.org"])
    assert _fetch(engine, "https://docs.python.org/3").allowed
    engine.allow_tool_for_session("web_search")
    assert engine.evaluate("web_search", {"query": "x"}, None).allowed


def test_a_listed_domain_covers_its_subdomains_and_no_look_alike(tmp_path):
    engine = _engine(tmp_path, allowed_domains=["python.org"])
    assert _fetch(engine, "https://docs.python.org/3").allowed
    assert not _fetch(engine, "https://evil-python.org/x").allowed  # only ends with the same letters


def test_a_domain_allowed_for_the_session_is_its_host_with_www_dropped_and_nothing_wider(tmp_path):
    engine = _engine(tmp_path)
    assert _fetch(engine, "https://api.github.com/x").needs_user
    engine.allow_domain_for_session("https://api.github.com/anything")
    assert _fetch(engine, "https://api.github.com/x").allowed
    engine.allow_domain_for_session("https://www.bbc.com/news/article")  # one spelling of one host
    assert "bbc.com" in engine.session_allow_domains
    assert _fetch(engine, "https://bbc.com/sport").allowed and _fetch(engine, "https://www.bbc.com/sport").allowed
    assert not _fetch(engine, "https://notbbc.com/x").allowed  # no registrable-domain widening
    engine.allow_domain_for_session("https://www2.example.org/a")
    assert "www2.example.org" in engine.session_allow_domains  # only "www." itself is dropped
    only = _engine(tmp_path)
    only.allow_domain_for_session("https://www.bbc.com/news/article")
    assert only.session_allow_domains == {"bbc.com"}


# -- what an override may change -----------------------------------------------------------------


def test_an_override_cannot_make_a_built_in_write_a_read(tmp_path):
    # The roots and the read-only gate both follow the class, so a downgrade would switch off both.
    overrides = _overrides({"write_file": RiskClass.READ})
    assert classify("write_file", None, overrides) is RiskClass.WRITE_LOCAL
    assert not _engine(tmp_path, plan=True, risk_overrides=overrides).evaluate("write_file", {"path": "../../escape.txt", "content": "x"}, None).allowed


def test_an_override_may_relax_a_plugin_tool_or_tighten_any(tmp_path):
    # what overrides are for: an MCP tool that defaults to external, taken down to a read
    assert classify("mcp__notion__search", SimpleNamespace(requires_approval=True, category="mcp"), _overrides({"mcp__notion__search": RiskClass.READ})) is RiskClass.READ
    assert classify("mcp__x__run", None, _overrides({"mcp__x__run": RiskClass.EXEC})) is RiskClass.EXEC


def test_an_override_cannot_quietly_make_a_declared_egress_a_read(tmp_path):
    assert classify("web_fetch", None, _overrides({"web_fetch": RiskClass.READ})) is RiskClass.EGRESS


# -- connector tools listed as reads that write ----------------------------------------
#
# These were catalogued as reads while writing to disk, which took them past the approval, the roots
# and read-only all at once. They are named here so an edit to the catalog cannot reopen it.

_WRITES_LISTED_AS_READS = ("github_clone", "github_pull")  # a repo tree on disk; a working tree changed


def test_a_connector_write_is_gated_whatever_its_metadata_or_an_override_says(tmp_path):
    from harness.integrations.connectors.catalog import approval_for_tool

    for name in _WRITES_LISTED_AS_READS:
        assert approval_for_tool(name) is True and classify(name) is RiskClass.EXTERNAL, name
    # the floor is read from the catalog, not from metadata a builder got wrong
    assert classify("github_clone", SimpleNamespace(requires_approval=False)) is RiskClass.EXTERNAL
    overrides = _overrides({"github_clone": RiskClass.READ})
    assert classify("github_clone", None, overrides) is RiskClass.EXTERNAL
    assert not _engine(tmp_path, plan=True, risk_overrides=overrides).evaluate("github_clone", {"repo": "octo/repo"}, None).allowed


def test_connector_reads_stay_reads_and_ask_nobody():
    from harness.integrations.connectors.catalog import approval_for_tool

    for name in ("email_search", "jira_get_issue"):
        assert approval_for_tool(name) is False and classify(name) is RiskClass.READ, name
    assert classify("email_search", None, _overrides({"email_search": RiskClass.READ})) is RiskClass.READ
    # the floor is about what leaves on the way out: reading your own mail or calendar is free
    for name in ("email_search", "gcal_list_events", "gmail_get_message", "jira_get_issue"):
        assert classify(name) is RiskClass.READ, name


@pytest.mark.parametrize("settings", [{"mode": Mode.READ_ONLY}, {"plan": True}])
def test_nothing_that_writes_or_sends_runs_in_read_only_or_the_rehearsal(tmp_path, settings):
    engine = _engine(tmp_path, **settings)
    for name in _WRITES_LISTED_AS_READS + ("web_fetch", "web_search"):
        assert not engine.evaluate(name, {}, SimpleNamespace(requires_approval=True)).allowed, f"{name} ran with {settings}"


# -- where a write lands -------------------------------------------------------------------------


def test_a_write_names_its_paths_or_says_it_could_not():
    assert write_paths("write_file", {"path": "a.txt"}) == (["a.txt"], True)
    assert write_paths("replace_in_file", {"path": "b.py"}) == (["b.py"], True)
    assert write_paths("write_file", {}) == ([], False)  # not found, so the caller fails closed
    assert write_paths("apply_patch", {"patch": IN_ROOT_PATCH}) == (["src/app.py"], True)
    assert write_paths("apply_unified_diff", {"diff": "--- a/old.py\n+++ b/new.py\n@@\n-a\n+b"}) == (["new.py"], True)


def test_a_write_whose_path_cannot_be_found_is_asked_about_until_full_access(tmp_path):
    # Made a write by an override, with nowhere to point at, it may not ride a session grant
    # unscoped. Full access asks nobody: there the person already handed that reach over.
    overrides = _overrides({"weird_writer": RiskClass.WRITE_LOCAL})
    engine = _engine(tmp_path, mode=Mode.WORKSPACE_WRITE, risk_overrides=overrides)
    engine.allow_tool_for_session("weird_writer")
    verdict = engine.evaluate("weird_writer", {"blob": "..."}, None)
    assert not verdict.allowed and verdict.needs_user and verdict.human_only
    assert _engine(tmp_path, mode=Mode.DANGER_FULL_ACCESS, risk_overrides=overrides).evaluate("weird_writer", {"blob": "..."}, None).allowed


def test_patch_scoping_follows_the_preset(tmp_path):
    """The patch's target is read from the blob and named, and the preset decides: full
    access applies it out of the roots, `workspace-write` asks with the path in the reason."""
    full, asking = _engine(tmp_path, mode=Mode.DANGER_FULL_ACCESS), _engine(tmp_path, mode=Mode.WORKSPACE_WRITE)
    assert full.evaluate("apply_patch", {"patch": CHANGES_ROOT_PATCH}, None).allowed
    outside = asking.evaluate("apply_patch", {"patch": CHANGES_ROOT_PATCH}, None)
    assert outside.needs_user and "not in a writable directory" in outside.reason
    assert asking.evaluate("apply_patch", {"patch": IN_ROOT_PATCH}, None).needs_user  # in the roots it asks too
    assert full.evaluate("apply_patch", {"patch": IN_ROOT_PATCH}, None).allowed


# -- a tool nobody declared is not a read ---------------------------------------------
#
# Until 2026-10-23 a tool missing from the risk tables, with no metadata, was a read: a new tool that
# sent something out ran unasked in every preset, read-only included.

_UNDECLARED = "lookup_someone"


def test_an_undeclared_tool_is_external_and_asks_or_is_refused(tmp_path):
    assert classify(_UNDECLARED) is RiskClass.EXTERNAL
    asked = _engine(tmp_path).evaluate(_UNDECLARED, {"name": "Jane"}, None)
    assert not asked.allowed and asked.needs_user
    for settings in ({"mode": Mode.READ_ONLY}, {"plan": True}):
        refused = _engine(tmp_path, **settings).evaluate(_UNDECLARED, {"name": "Jane"}, None)
        assert not refused.allowed and not refused.needs_user


def test_a_tool_that_says_what_it_is_keeps_its_word():
    assert classify(_UNDECLARED, SimpleNamespace(requires_approval=False)) is RiskClass.READ
    assert classify(_UNDECLARED, SimpleNamespace(requires_approval=True)) is RiskClass.EXTERNAL
