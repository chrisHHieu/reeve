import { useEffect, useState } from "react";
import {
  getBrowserConfig,
  setBrowserConfig,
  type BrowserConfig,
} from "../../api";
import { Toggle } from "../Toggle";

// The built-in browser is ONE MCP server, Playwright's. Its detail page sets how it runs
// instead of showing raw command JSON: two modes (a private per-session browser, or
// attaching to one the user already has open) and headless. The engine pickers
// (Chrome DevTools, Stagehand) were removed on 2026-09-30; Chrome DevTools came back the next
// day as its own built-in server, `chrome-devtools`, which these settings also drive.

export function BrowserBackendPanel({ onChanged }: { onChanged: () => void }) {
  const [cfg, setCfg] = useState<BrowserConfig | null>(null);
  const [mode, setMode] = useState("isolated");
  const [endpoint, setEndpoint] = useState("");
  const [headless, setHeadless] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBrowserConfig()
      .then((c) => {
        setCfg(c);
        setMode(c.mode);
        setEndpoint(c.endpoint);
        setHeadless(c.headless);
      })
      .catch(() => {});
  }, []);

  if (!cfg) return null;

  const dirty =
    mode !== cfg.mode ||
    endpoint !== cfg.endpoint ||
    headless !== cfg.headless;

  const save = async () => {
    setBusy(true);
    setMsg(null);
    setError(null);
    const res = await setBrowserConfig({
      mode,
      endpoint,
      headless,
    }).catch(() => ({ ok: false, error: "couldn't reach the server" } as BrowserConfig));
    setBusy(false);
    if (!res.ok) {
      setError(res.error || "couldn't save");
      return;
    }
    setCfg(res);
    setMode(res.mode);
    setEndpoint(res.endpoint);
    setHeadless(res.headless);
    setMsg("Saved — the next turn uses it.");
    onChanged();
  };

  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13px] outline-none focus:border-accent";
  const label = "block text-[12px] text-muted mb-1";

  return (
    <div className="space-y-4" data-testid="browser-backend-panel">
      <div>
        <div className="text-[12px] font-semibold text-muted mb-1">Browser</div>
        <p className="text-[12px] text-faint" data-testid="browser-engine">
          Playwright: the agent reads pages as an accessibility snapshot, clicks, fills
          and checks network and console. Every session gets its own browser. For deeper
          inspection (performance traces, request bodies), switch on the built-in{" "}
          <span className="font-mono">chrome-devtools</span> server in the MCP list; it needs
          Node.js and follows these settings.
        </p>
      </div>

      <div>
        <div className="text-[12px] font-semibold text-muted mb-1.5">Browser mode</div>
        <div
          className="inline-flex gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]"
          role="radiogroup"
          aria-label="Browser mode"
        >
          {[
            { id: "isolated", label: "Private browser" },
            { id: "attach", label: "Use a running browser" },
          ].map((option) => {
            const active = mode === option.id;
            return (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={active}
                className={
                  "px-3 py-1.5 rounded-lg text-[13px] whitespace-nowrap transition-colors " +
                  (active
                    ? "bg-panel text-ink font-medium shadow-sm ring-1 ring-line"
                    : "text-muted hover:text-ink")
                }
                data-testid={`browser-mode-${option.id}`}
                onClick={() => setMode(option.id)}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        {mode === "attach" && (
          <div className="mt-2.5">
            <label className={label} htmlFor="browser-endpoint">
              CDP endpoint
            </label>
            <input
              id="browser-endpoint"
              className={input}
              type="text"
              placeholder="http://127.0.0.1:9222"
              value={endpoint}
              data-testid="browser-endpoint"
              onChange={(e) => setEndpoint(e.target.value)}
            />
            <p className="text-[12px] text-faint mt-1">
              Start Chrome with <span className="font-mono">--remote-debugging-port=9222</span>{" "}
              to keep its tabs and sign-ins.
            </p>
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <span className="text-[13px] text-ink flex-1">
          Headless
          <span className="block text-[12px] text-faint">
            Hide the browser window. Only applies to the private-browser mode.
          </span>
        </span>
        <Toggle
          checked={headless}
          onChange={() => setHeadless((v) => !v)}
          title="Run the browser headless"
        />
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-lg border border-accent bg-accent px-4 py-1.5 text-[13px] font-medium text-white hover:brightness-105 disabled:opacity-40"
          onClick={() => void save()}
          disabled={busy || !dirty}
          data-testid="browser-save"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <span className="min-w-0 flex-1 text-[12px]" data-testid="browser-msg">
          {error ? <span className="text-warnInk">{error}</span> : msg && <span className="text-ok">{msg}</span>}
        </span>
      </div>

      <div className="text-[12px] text-faint">
        Runs: <span className="font-mono break-all" data-testid="browser-launch">{cfg.launch}</span>
        {cfg.connected && cfg.tool_count != null && (
          <span className="text-ok"> · live, {cfg.tool_count} tools</span>
        )}
      </div>
    </div>
  );
}
