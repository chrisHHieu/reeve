import { useState } from "react";
import { connectConnector, type Connector } from "../../api";
import { McpOneClick } from "./McpOneClick";

const CONNECT = "text-[13px] px-3 py-1.5 rounded-lg bg-accent text-[color:var(--canvas)] shrink-0 disabled:opacity-50";

/** How a connector is connected by hand: its steps, its fields, and Connect, which the server checks
 *  before it keeps anything. A connector with a one-click sign-in offers that first, unless the
 *  caller already shows it beside this (`manualOnly`, the add sheet's Manual pane). */
export function ConnectSetup({ c, onConnected, manualOnly = false }: { c: Connector; onConnected: () => void; manualOnly?: boolean }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connect = async () => {
    setBusy(true);
    setError(null);
    const result = await connectConnector(c.name, values);
    setBusy(false);
    if (result.ok) onConnected();
    else setError(result.error || "could not connect");
  };

  return (
    <div className="border-t border-line px-3.5 py-3 space-y-3">
      {c.one_click && !manualOnly && (
        // The sign-in pane of the add sheet, so the waiting, failed and reopen states read the same.
        <div className="space-y-2" data-testid="mcp-connect">
          <McpOneClick c={c} onConnected={onConnected} inline />
          {c.fields.length > 0 && <div className="text-[12px] text-faint">or connect manually:</div>}
        </div>
      )}
      {c.instructions.length > 0 && (
        <ol className="list-decimal pl-4 text-[13px] text-muted leading-relaxed space-y-1">
          {c.instructions.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      {c.fields.map((field) => (
        <label className="conn-field" key={field.key}>
          <span className="conn-field-label">
            {field.label}
            {!field.required && <em> (optional)</em>}
          </span>
          <input
            type={field.secret ? "password" : "text"}
            placeholder={field.placeholder}
            value={values[field.key] || ""}
            spellCheck={false}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
          />
          {field.help && <span className="conn-field-help">{field.help}</span>}
        </label>
      ))}
      <div>
        <button className={CONNECT} onClick={connect} disabled={busy}>
          {busy ? "Validating…" : "Connect"}
        </button>
      </div>
      {error && <div className="text-[13px] text-danger">{error}</div>}
    </div>
  );
}
