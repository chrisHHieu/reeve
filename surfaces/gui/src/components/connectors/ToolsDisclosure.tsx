import { useState } from "react";
import { updateConnectorTools, type Connector } from "../../api";
import type { ConnectorTool } from "../../api";
import { FoldBody, FoldHead, Switch } from "../settings/primitives";
import { TAG_QUIET, TAG_WARN } from "./ui";

// The Tools fold on every connector page, closed until opened: the control is
// always there and never loud. Open, it is a row per tool with a read or write tag, and a write
// always asks first.
//
// A catalog long enough to declare groups (Slack: read / write / admin) draws one
// heading per group with a switch for the whole group, so fifty tools are a decision about
// three things rather than fifty checkboxes. Admin tools start off; the heading says so.
//
// It opens and closes with the shared fold (2026-10-10): a native <details> snapped open with no
// motion at all, and a tool was a checkbox where every other on/off in Settings is a switch.
const GROUPS: Record<string, { title: string; note?: string }> = {
  read: { title: "Read" },
  write: { title: "Write", note: "asks first" },
  admin: { title: "Manage channels, people and groups", note: "off until you turn them on · asks first" },
};

export function ToolsDisclosure({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const all = c.tools ?? [];
  if (!all.length) return null;
  const switchedOn = all.filter((t) => t.enabled).length;
  const grouped = all.some((t) => t.group);

  const row = (tool: ConnectorTool) => (
    <div key={tool.name} className="connector-tool-row" title={`${tool.name} — ${tool.description}`}>
      <span className="connector-tool-name">{tool.label}</span>
      <span className={tool.kind === "write" ? TAG_WARN : TAG_QUIET}>
        {tool.kind === "write" ? "asks first" : "read"}
      </span>
      <Switch
        checked={tool.enabled}
        label={tool.label}
        onChange={async (next) => {
          await updateConnectorTools(c.name, { [tool.name]: next });
          onChanged();
        }}
      />
    </div>
  );

  const section = (key: string) => {
    const tools = all.filter((t) => t.group === key);
    if (tools.length === 0) return null;
    const on = tools.filter((t) => t.enabled).length;
    const meta = GROUPS[key] ?? { title: key };
    const setAll = async (value: boolean) => {
      await updateConnectorTools(c.name, Object.fromEntries(tools.map((t) => [t.name, value])));
      onChanged();
    };
    return (
      <div key={key} data-testid={`tools-group-${key}`}>
        <div className="connector-tool-group">
          <span className="min-w-0 flex-1">
            {meta.title}{" "}
            <span className="connector-tool-group-meta">
              · {on} of {tools.length}
              {meta.note ? ` · ${meta.note}` : ""}
            </span>
          </span>
          <button type="button" className="settings-text-btn" data-testid={`tools-all-${key}`} onClick={() => setAll(on < tools.length)}>
            {on < tools.length ? "Turn all on" : "Turn all off"}
          </button>
        </div>
        {tools.map(row)}
      </div>
    );
  };

  return (
    <section className="settings-card-block connector-tools" data-testid="connector-tools">
      <div className="settings-panel">
        <FoldHead open={open} onToggle={() => setOpen(!open)} aside={`${switchedOn} of ${all.length} on`} testId="tools-fold">
          Tools
        </FoldHead>
        <FoldBody open={open}>
          <div className="connector-tool-list">
            {grouped ? ["read", "write", "admin"].map(section) : all.map(row)}
          </div>
        </FoldBody>
      </div>
    </section>
  );
}
