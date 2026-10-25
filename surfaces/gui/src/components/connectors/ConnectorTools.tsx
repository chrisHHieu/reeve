import { updateConnectorTools, type Connector } from "../../api";
import { SettingsCard, SettingsRow, Switch } from "../settings/primitives";

/** Every tool a connector brings, one switch each: what it does, its name and kind, and whether a
 *  call asks first. A tool switched off is not offered to the agent. */
export function ConnectorTools({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const tools = c.tools ?? [];
  const on = tools.filter((tool) => tool.enabled).length;
  const flip = (name: string, enabled: boolean) => updateConnectorTools(c.name, { [name]: enabled }).then(onChanged);
  return (
    <SettingsCard
      title="Tools exposed to Reeve"
      action={
        tools.length ? (
          <span className="settings-heading-count">
            {on} of {tools.length} on
          </span>
        ) : undefined
      }
      testId={`connector-tools-${c.name}`}
    >
      {tools.length === 0 ? <div className="settings-empty">No tools for this connector yet.</div> : null}
      {tools.map((tool) => (
        <SettingsRow
          key={tool.name}
          title={tool.label}
          detail={
            <>
              <span className="settings-item-line clamp-2" title={tool.description}>
                {tool.description}
              </span>
              <span className="settings-item-meta">
                {tool.name} · {tool.kind}
                {tool.requires_approval === false ? "" : " · asks approval"}
              </span>
            </>
          }
        >
          <Switch checked={tool.enabled} label={tool.label} testId={`connector-tool-${tool.name}`} onChange={(next) => void flip(tool.name, next)} />
        </SettingsRow>
      ))}
    </SettingsCard>
  );
}
