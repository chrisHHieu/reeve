import { useState } from "react";
import { setSlackUserToken, type Connector } from "../../api";
import { GRP, GRP_H, PILL_ACCENT, PILL_LINE, ROW } from "./ui";

// The optional user token. Slack answers search, reminders and status only for a user
// token, so the agent's Slack tools for those say "needs a user token" until one is added
// here. It is checked with Slack before it is kept and shown only as
// present/absent afterwards.
export function SlackUserToken({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const has = !!c.has_user_token;

  const save = async (value: string) => {
    setBusy(true);
    setError("");
    const res = await setSlackUserToken(value);
    setBusy(false);
    if (!res.ok) setError(res.error ?? "Could not save the token");
    else setToken("");
    onChanged();
  };

  if (!c.connected) return null;
  return (
    <div data-testid="slack-user-token">
      <div className={GRP_H}>Search &amp; reminders</div>
      <div className={GRP}>
        <div className={ROW + " flex-wrap"}>
          <span className="min-w-0 flex-1 text-[13px] text-muted">
            Slack keeps search, reminders and status away from bots.{" "}
            {has ? "A user token is set." : "Add a user token (xoxp-…) to use them."}
          </span>
        </div>
        <div className={ROW}>
          <input
            type="password"
            className="min-w-0 flex-1 text-[13px] bg-paper border border-line rounded px-2 py-1"
            placeholder="xoxp-…"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            aria-label="Slack user token"
            data-testid="slack-user-token-input"
          />
          <button
            className={PILL_ACCENT}
            disabled={busy || !token.trim()}
            data-testid="slack-user-token-save"
            onClick={() => void save(token)}
          >
            Save
          </button>
          {has && (
            <button
              className={PILL_LINE}
              disabled={busy}
              data-testid="slack-user-token-remove"
              onClick={() => void save("")}
            >
              Remove
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="text-[12px] text-danger px-4 pt-1.5" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
