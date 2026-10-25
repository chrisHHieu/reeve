import { useState } from "react";
import { setConnectorToolsets, type Connector } from "../../api";
import { GRP, GRP_H, PILL_ACCENT, ROW, TAG_WARN } from "./ui";

// The vendor server's tool groups (GitHub's toolsets), on the connector page of a
// connection that is served by it. Turning a group on is a checkbox; a group that needs a
// permission the sign-in lacks says so and offers ONE "Grant access" — the same
// sign-in the connector already has, asked again for the union of what the chosen groups
// need. Nothing here starts a sign-in on its own: a permission is never asked for as a
// side effect of a checkbox (owner, 2026-09-30).
export function ToolsetsGroup({
  c,
  onChanged,
  onGrant,
}: {
  c: Connector;
  onChanged: () => void;
  onGrant: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sets = c.toolsets ?? [];
  if (sets.length === 0) return null;
  const missing = Array.from(new Set(sets.filter((s) => s.enabled).flatMap((s) => s.needs_scopes)));

  const toggle = async (id: string, on: boolean) => {
    setBusy(true);
    setError("");
    const next = sets.filter((s) => (s.id === id ? on : s.enabled)).map((s) => s.id);
    const res = await setConnectorToolsets(c.name, next);
    setBusy(false);
    if (!res.ok) setError(res.error ?? "Could not save");
    onChanged();
  };

  return (
    <div data-testid="toolsets-group">
      <div className={GRP_H}>Tool groups</div>
      <div className={GRP}>
        {sets.map((s) => (
          <label
            key={s.id}
            className={ROW + (s.locked ? "" : " cursor-pointer")}
            data-testid={`toolset-${s.id}`}
            title={s.description}
          >
            <input
              type="checkbox"
              checked={s.enabled}
              disabled={busy || s.locked}
              onChange={(e) => void toggle(s.id, e.target.checked)}
            />
            <span className="min-w-0 flex-1">
              <span className="text-[13px] font-medium">{s.label}</span>
              <span className="text-[12px] text-muted"> · {s.description}</span>
            </span>
            {s.enabled && s.needs_scopes.length > 0 && (
              <span className={TAG_WARN} data-testid={`toolset-needs-${s.id}`}>
                needs permission
              </span>
            )}
          </label>
        ))}
        {missing.length > 0 && (
          <div className={ROW}>
            <span className="min-w-0 flex-1 text-[13px] text-muted">
              Some groups need permission GitHub has not been given yet ({missing.join(", ")}).
            </span>
            <button className={PILL_ACCENT} data-testid="toolsets-grant" onClick={onGrant}>
              Grant access
            </button>
          </div>
        )}
      </div>
      {error && (
        <div className="text-[12px] text-danger px-4 pt-1.5" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
