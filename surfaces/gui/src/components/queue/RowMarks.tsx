import { formatRemoteEnvironmentReadout } from "../../api";
import type { SessionInfo } from "../../types";
import { Icon } from "../Icon";

// The small marks at a queue row's edge: whether it is working, what it listens to, where it runs.

/** Working now, or asleep until a timer it set. A steady dot: one pulsing on every running row
 *  is motion that never stops at the edge of the eye. */
export function LiveDot({ state }: { state?: SessionInfo["liveness"] }) {
  if (state !== "working" && state !== "sleeping") return null;
  const working = state === "working";
  return <span className={"w-1.5 h-1.5 rounded-full shrink-0 " + (working ? "bg-accent" : "bg-faint/60")} title={working ? "Working now" : "Sleeping (will wake itself)"} aria-hidden />;
}

/** The session listens to a channel. Neutral: the sidebar has no connector colours to give it. */
export function ConnectorDot({ subs }: { subs?: string[] }) {
  if (!subs?.length) return null;
  return <span className="w-1.5 h-1.5 rounded-full bg-faint shrink-0" data-brand={subs[0]} title={subs.join(", ")} />;
}

/** A session on an SSH host, named with its host and folder, so it never reads as a
 *  folder here. A local row has none. The folder comes from `remote_workspace`, the host's own
 *  path; a row from before that field passes `workspace` through the same formatter, which turns a
 *  local spelling away. */
export function RemoteIcon({ s }: { s: SessionInfo }) {
  if (!s.environment_id) return null;
  const where = s.environment ? formatRemoteEnvironmentReadout(s.environment, s.remote_workspace ?? s.workspace) : "SSH host";
  return (
    <span className="text-faint shrink-0 flex items-center" data-testid="session-remote" title={`Remote · ${where}`}>
      <Icon name="monitor" size={11} />
    </span>
  );
}
