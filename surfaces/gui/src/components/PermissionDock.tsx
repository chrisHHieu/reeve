// The permission dock (P1 / ledger 5.7): a child's parked ask above the composer, and
// the only surface that answers it.
//
// The dock belongs to the ANCESTOR you are viewing (a child's ask must not be answered only
// from inside the child's own read-only session), which is why App selects the request from
// the active session's subtree and renders this above the composer. It is the same decision
// card as every other ask; what it adds is WHO asks, because the person is looking at the
// parent and the question comes from somewhere they are not.

import { useRef } from "react";
import type { PermissionRequestInfo, PermissionReply } from "../api";
import {
  Decision,
  DecisionActions,
  DecisionBody,
  DecisionButton,
  DecisionSpacer,
} from "./decision/Decision";
import { useDecisionKeyboard } from "./primitives/decisionKeys";

/** The tool hint a person reads, the `permission.tool.<name>.description` map. */
export function permissionTitle(request: PermissionRequestInfo): string {
  return request.permission === "task" ? "Launch sub-agents" : request.permission;
}

/** The second line: the description for a spawn, else the value the ruleset matched. */
export function permissionDetail(request: PermissionRequestInfo): string {
  if (request.permission === "task") {
    const description = request.metadata?.description;
    return typeof description === "string" ? description : "";
  }
  return request.patterns[0] ?? "";
}

export function PermissionDock({
  request,
  responding,
  onReply,
  from,
}: {
  request: PermissionRequestInfo;
  /** True while a reply is in flight — the buttons disable rather than double-send. */
  responding: boolean;
  onReply: (reply: PermissionReply) => void;
  /** The subagent that asks, when it is not the session on screen. */
  from?: string;
}) {
  const detail = permissionDetail(request);
  const dockRef = useRef<HTMLDivElement>(null);
  // 1 and 2 allow, ↑↓ moves, Enter confirms, Escape denies (the only refusal this dock has,
  // so it is the honest dismissal).
  useDecisionKeyboard(dockRef, { onDismiss: () => onReply("reject"), enabled: !responding });
  const spawn = request.permission === "task";
  return (
    <div className="dec-above">
      <Decision
        icon={spawn ? "sparkle" : "shield"}
        title={
          spawn ? (
            <span data-testid="permission-title">{permissionTitle(request)}</span>
          ) : (
            <>
              Allow <code data-testid="permission-title">{permissionTitle(request)}</code>?
            </>
          )
        }
        context={from ? `Asked by ${from}` : "Asked by a subagent"}
        className="permission-dock"
        testId="permission-dock"
        innerRef={dockRef}
        label="Permission"
      >
        {detail && (
          <DecisionBody code={!spawn}>
            <span data-testid="permission-detail">{detail}</span>
          </DecisionBody>
        )}
        <DecisionActions>
          <DecisionButton primary data-testid="permission-once" disabled={responding} onClick={() => onReply("once")}>
            Allow once
          </DecisionButton>
          <DecisionButton data-testid="permission-always" disabled={responding} onClick={() => onReply("always")}>
            Allow always
          </DecisionButton>
          <DecisionSpacer />
          <DecisionButton
            quiet
            className="deny"
            data-testid="permission-deny"
            disabled={responding}
            onClick={() => onReply("reject")}
          >
            Deny
          </DecisionButton>
        </DecisionActions>
      </Decision>
    </div>
  );
}
