"""ApprovalsMixin: turning answers to approval prompts into outcomes, and auditing autonomy."""

from __future__ import annotations

import json
from typing import Any

from harness.loop import ApprovalOutcome
from harness.safety.permissions import Mode, standing_rule_candidate
from harness.safety.risk import RiskClass, classify

# The grants that outlive one call, and so need to be offered for the tool in question.
_WIDE_GRANTS = (ApprovalOutcome.ALWAYS_TOOL, ApprovalOutcome.ALWAYS_COMMAND, ApprovalOutcome.ALWAYS_DOMAIN)

# The three presets by how much runs unasked. Older spellings go through `Mode.migrated` before
# ranking, and the plan overlay is not a rung on this ladder.
_AUTONOMY_RANK = {"read-only": 0, "workspace-write": 2, "danger-full-access": 4}


def grant_offered(outcome: ApprovalOutcome, request) -> bool:
    """Whether a persistent grant is legitimately offered for this tool, mirroring what the
    approval card actually renders.

    - ALWAYS_TOOL covers a whole tool with any argument, so it is withheld from the shell (the
      command-scoped grant is narrower), from `save_skill` (every proposal gets its own review),
      from anything that reaches off the machine (connectors and MCP tools, where "always allow
      send_message" would cover every future recipient) and from egress that carries a URL
      ("always allow web_fetch" would cover every future destination, and the domain grant is the
      one offered). Egress with a fixed destination, like web search, keeps it.
    - ALWAYS_COMMAND only means something for the shell tool.
    - ALWAYS_DOMAIN only means something for a tool that carries a URL.
    """
    name = getattr(request, "tool_name", "")
    metadata = getattr(request, "metadata", None)
    arguments = getattr(request, "arguments", None) or {}
    risk = classify(name, metadata)

    if outcome is ApprovalOutcome.ALWAYS_COMMAND:
        return risk is RiskClass.EXEC
    if outcome is ApprovalOutcome.ALWAYS_DOMAIN:
        return risk is RiskClass.EGRESS and bool(arguments.get("url"))
    if outcome is ApprovalOutcome.ALWAYS_TOOL:
        if risk in (RiskClass.EXEC, RiskClass.EXTERNAL):
            return False
        if risk is RiskClass.EGRESS and arguments.get("url"):
            return False
        if getattr(metadata, "category", "") == "connector":
            return False
        return name != "save_skill"
    return True


def _autonomy_rank(mode: Any) -> int:
    try:
        return _AUTONOMY_RANK.get(Mode.migrated(mode)[0].value, 0)
    except (TypeError, ValueError):
        return 0


class ApprovalsMixin:
    def approval_prompt_data(self, session_id: str, request) -> dict[str, Any]:
        """The extra payload of a parked approval.

        It always carries the tool name and arguments, so the GUI can draw the same humanized
        card it shows live; without them a reopened session fell back to the raw "Run `tool`?".
        An automation run also carries the owning task and, when the call is eligible, the exact
        target a standing rule would pin. The GUI offers "Allow every time" only when both are
        present, and only in the app, never on buttons mirrored to Slack.
        """
        data: dict[str, Any] = {
            "tool": request.tool_name,
            "arguments": getattr(request, "arguments", None) or {},
        }
        # Two facts a parked card cannot derive from the tool and its arguments. Without the
        # category it told people a connector call stayed on their computer, and without the
        # presentation it fell back to the tool's raw name.
        category = getattr(getattr(request, "metadata", None), "category", "")
        if category:
            data["category"] = category
        if getattr(request, "presentation", None):
            data["presentation"] = request.presentation
        # The rest of what the live card read (axis, provenance, read-only offer, standing
        # target, extras), so the parked card is that card. The four keys above are already in
        # the payload and are not stored twice.
        card = getattr(request, "card", None)
        if isinstance(card, dict):
            data["card"] = {
                key: value
                for key, value in card.items()
                if key not in ("name", "arguments", "presentation", "category")
            }
        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return data
        data.update({"task_id": task.id, "task_title": task.title})
        target = standing_rule_candidate(
            request.tool_name, getattr(request, "arguments", None) or {}, getattr(request, "metadata", None)
        )
        if target:
            data["standing_target"] = target
        return data

    def mint_task_rule(self, session_id: str, tool_name: str, arguments: Any, metadata: Any = None) -> bool:
        """Persist a standing rule a person minted with "Allow every time" on a run's approval
        card. The server validates and does not trust the card: the session must be an
        automation run, and the call must be eligible for a rule (external risk, a declared
        target argument, a non-empty target). The rule also applies to the live engine, so the
        run's next call is allowed."""
        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return False
        target = standing_rule_candidate(tool_name, arguments or {}, metadata)
        if not target or not task.add_rule(tool_name, target):
            return False
        self.task_store.save(task)
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.permissions.task_rules.setdefault(tool_name, set()).add(target)
        self._audit(
            session_id,
            tool=tool_name,
            arguments=arguments or {},
            stage="standing_rule_minted",
            status="granted",
            reason=f"allow every time: {tool_name} → {target} (task {task.id})",
        )
        return True

    def approval_outcome(self, resolution: str, request, session_id: str):
        """Map an approval resolution from any surface to an `ApprovalOutcome`, handling the
        task-persistent "always_task" beside the session-scoped ones.

        The caller is not trusted: a grant that no UI offers for this tool is downgraded to a
        one-time approval and not honoured. The GUI hides the broad "always allow" for the
        shell, connectors and `save_skill`, and the Slack mirror renders only approve and deny,
        but `POST /v1/inbox/{id}/resolve` takes a raw string, so without this check any local
        API caller could mint a session-wide any-argument shell grant.
        """
        if isinstance(resolution, str) and resolution.lstrip().startswith("{"):
            return self._edited_outcome(resolution, request)
        if resolution == "always_task":
            minted = self.mint_task_rule(
                session_id, request.tool_name, getattr(request, "arguments", None), getattr(request, "metadata", None)
            )
            if not minted:
                self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        try:
            outcome = ApprovalOutcome(resolution)
        except ValueError:
            if resolution == "allow":
                return ApprovalOutcome.ONCE
            if resolution != "always":
                return ApprovalOutcome.DENY
            outcome = ApprovalOutcome.ALWAYS_TOOL
        if outcome in _WIDE_GRANTS and not grant_offered(outcome, request):
            self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        return outcome

    @staticmethod
    def _edited_outcome(resolution: str, request) -> ApprovalOutcome:
        """A JSON resolution is the "edit before running" answer: the person's arguments are
        parked on the request, and the engine re-checks them as a NEW call. Anything malformed is
        a refusal, since a decision nobody can parse must never run something."""
        try:
            payload = json.loads(resolution)
        except json.JSONDecodeError:
            return ApprovalOutcome.DENY
        if not isinstance(payload, dict) or payload.get("decision") != "modify":
            return ApprovalOutcome.DENY
        edited = payload.get("arguments")
        if not isinstance(edited, dict) or not edited:
            return ApprovalOutcome.DENY
        request.edited_arguments = edited
        return ApprovalOutcome.MODIFY

    # -- the audit trail of autonomy -----------------------------------------------------

    def _audit(self, session_id: str, *, tool: str, arguments: Any, stage: str, status: str, reason: str) -> None:
        """Append one audit row. Auditing is best effort: a failure here never costs the action."""
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": tool,
                    "arguments": arguments,
                    "stage": stage,
                    "status": status,
                    "reason": reason,
                }
            )
        except Exception:
            pass

    def audit_autonomy_change(self, session_id: str, kind: str, before: Any, after: Any) -> None:
        """Record a change to how much the agent may do unsupervised: the permission mode, the
        or the plan overlay. Without it, "who turned on auto mode, and when" is
        unanswerable from the audit store, at odds with the per-call trail the rest of the
        engine keeps. Raising autonomy is flagged so it can be filtered."""
        if kind == "mode":
            raised = _autonomy_rank(after) > _autonomy_rank(before)
        elif kind == "plan":
            # Turning the plan overlay ON adds the read-only floor, so fewer actions run unasked.
            # The direction is the opposite of an ordinary boolean capability.
            raised = bool(before) and not bool(after)
        else:
            raised = bool(after) and not bool(before)
        self._audit(
            session_id,
            tool="",
            arguments={},
            stage=f"{kind}_changed",
            status="raised" if raised else "lowered",
            reason=f"{kind}: {before} → {after}",
        )

    def _audit_grant_refused(self, session_id: str, request, resolution: str) -> None:
        self._audit(
            session_id,
            tool=getattr(request, "tool_name", ""),
            arguments=getattr(request, "arguments", None) or {},
            stage="grant_refused",
            status="downgraded",
            reason=f"resolution {resolution!r} is not offered for this tool — applied as a one-time approval",
        )
