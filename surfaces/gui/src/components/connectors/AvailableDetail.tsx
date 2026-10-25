import { useState } from "react";
import { type Connector } from "../../api";
import { FoldBody, FoldHead, SettingsCard } from "../settings/primitives";
import { AddConnectionModal } from "./AddConnectionModal";
import { ConnectorHead } from "./DetailParts";
import { TAG_QUIET } from "./ui";

// A connector's page before it is connected: what it is for and what it will be
// able to reach, while no credentials exist yet. The About paragraph, the Access lines as they
// really are, and its tools behind a closed fold for whoever wants that much (no switches here:
// those are on the connected page). Connect opens the same sheet as the list's button.

export function AvailableDetail({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const [connecting, setConnecting] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const tools = c.tools ?? [];
  const toolsets = c.toolsets ?? [];
  const access = c.access ?? [];

  return (
    <div className="connector-page" data-testid="available-detail">
      <div>
        <ConnectorHead
          c={c}
          tone="off"
          state="Not connected"
          action={
            <button
              type="button"
              className="settings-text-btn accent"
              data-testid="available-connect"
              onClick={() => setConnecting(true)}
            >
              Connect
            </button>
          }
        />
        {c.blurb ? <p className="settings-provider-blurb">{c.blurb}</p> : null}
        {c.about ? <p className="settings-provider-blurb">{c.about}</p> : null}
      </div>

      {access.length > 0 && (
        <div>
          <SettingsCard title="Access" testId="available-access">
            {access.map((line) => (
              <div key={line} className="connector-line">
                {line}
              </div>
            ))}
          </SettingsCard>
          <p className="connector-foot">Keys and tokens are stored only on this computer. Disconnect anytime.</p>
        </div>
      )}

      {toolsets.length > 0 && (
        <div>
          <SettingsCard title={`Tools from ${c.title}\u2019s own server`} testId="available-toolsets">
            {toolsets.map((t) => (
              <div key={t.id} className="connector-line">
                <span className="min-w-0 flex-1">
                  <span className="connector-line-title">{t.label}</span>
                  <span className="connector-line-sub">{t.description}</span>
                </span>
                {!t.locked && !t.enabled && <span className={TAG_QUIET}>off until you turn it on</span>}
              </div>
            ))}
          </SettingsCard>
          <p className="connector-foot">
            Connect to see each tool. Anything that changes something asks first; you choose the
            groups on the connector page.
          </p>
        </div>
      )}

      {tools.length > 0 && (
        <section className="settings-card-block">
          <div className="settings-panel">
            <FoldHead open={showTools} onToggle={() => setShowTools(!showTools)} testId="available-tools-toggle">
              {tools.length} tool{tools.length === 1 ? "" : "s"}{" "}
              {toolsets.length ? "Reeve adds on this computer" : "this connector adds"}
            </FoldHead>
            <FoldBody open={showTools}>
              <div className="connector-tool-list">
                {tools.map((t) => (
                  <div key={t.name} className="connector-line">
                    <span className="min-w-0 flex-1">
                      <span className="connector-line-title">{t.label}</span>
                      <span className="connector-line-sub">{t.description}</span>
                    </span>
                    {t.kind !== "read" && <span className={TAG_QUIET}>asks first</span>}
                  </div>
                ))}
              </div>
            </FoldBody>
          </div>
        </section>
      )}

      {connecting && <AddConnectionModal c={c} onClose={() => setConnecting(false)} onChanged={onChanged} />}
    </div>
  );
}
