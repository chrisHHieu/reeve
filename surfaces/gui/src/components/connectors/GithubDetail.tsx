import { useState } from "react";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import type { DetailProps } from "./ConnectorsSection";
import { DisconnectConnector } from "./DisconnectButton";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { ToolsetsGroup } from "./ToolsetsGroup";
import { FOOT, GRP, ROW } from "./ui";

// GitHub's page. GitHub's own MCP server is the toolset: the page says how this
// computer is signed in, lets the person choose which of the server's groups of tools the agent
// gets, and lists the two tools that are Reeve's (clone, and pulling into a clone, which act on this
// machine).

export function GithubDetail({ c, onChanged }: DetailProps) {
  const [granting, setGranting] = useState(false);
  const throughServer = c.mode === "mcp";
  return (
    <div data-testid="github-page">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="GitHub" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">GitHub</h2>
          <div className="text-[13px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="github-mode-badge">{throughServer ? "Connected · GitHub's own tools" : "Connected · personal access token"}</span>
              </>
            ) : (
              <span>Not connected</span>
            )}
          </div>
        </div>
        <DisconnectConnector c={c} onChanged={onChanged} />
      </div>

      {c.connected && !throughServer && (
        <div className={GRP} data-testid="github-manual-card">
          <div className={ROW + " text-[13px] text-muted"}>
            The token could not reach GitHub&rsquo;s tool server, so only Clone and Update-a-clone are available. Reconnect to
            get the full set.
            {c.mcp_error ? ` (${c.mcp_error})` : ""}
          </div>
        </div>
      )}

      {throughServer && <ToolsetsGroup c={c} onChanged={onChanged} onGrant={() => setGranting(true)} />}
      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        Reading and writing GitHub is the server&rsquo;s own toolset, and a write asks first. Clone and Update a clone run on
        this computer.
      </div>
      {granting && <AddConnectionModal c={c} title="Grant GitHub access" regrant onClose={() => setGranting(false)} onChanged={onChanged} />}
    </div>
  );
}
