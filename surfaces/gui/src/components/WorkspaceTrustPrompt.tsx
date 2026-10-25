import { useState } from "react";
import { setWorkspaceTrusted, type WorkspaceCommandTrust } from "../api";

// A project's `.reeve/config.toml` lists commands to run without asking; they count only once the
// person trusts that folder. This is where they are asked.
export function WorkspaceTrustPrompt({ request, onClose }: { request: WorkspaceCommandTrust; onClose: () => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const trust = async () => {
    setSaving(true);
    setError("");
    const saved = await setWorkspaceTrusted(request.workspace, true).catch(() => null);
    setSaving(false);
    if (saved?.ok) onClose();
    else setError(saved?.error || "Could not save workspace trust.");
  };

  return (
    <div className="gate-overlay" role="dialog" aria-modal="true" aria-labelledby="workspace-trust-title">
      <div className="gate max-w-[560px]">
        <div className="gate-mark">✦</div>
        <h2 id="workspace-trust-title">Trust this workspace&rsquo;s commands?</h2>
        <p className="gate-sub">
          This project wants Reeve to run the commands below without asking each time. Trusting it
          covers this exact folder, including later changes to its settings, until you take the
          trust back in Settings.
        </p>
        <div className="rounded-lg border border-line bg-paper px-3 py-2.5 max-h-48 overflow-y-auto">
          {request.requested_commands.map((command) => (
            <code key={command} className="block text-[13px] py-1 text-ink">
              {command}
            </code>
          ))}
        </div>
        <div className="text-[12px] text-muted mt-2 break-all">{request.workspace}</div>
        {error && <div className="gate-error">{error}</div>}
        <div className="gate-foot justify-end gap-2">
          <button className="btn" onClick={onClose} disabled={saving}>
            Keep asking
          </button>
          <button className="btn primary" onClick={() => void trust()} disabled={saving}>
            {saving ? "Saving…" : "Trust workspace"}
          </button>
        </div>
      </div>
    </div>
  );
}
