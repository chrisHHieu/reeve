import { useState } from "react";

import { disconnectConnector, type Connector } from "../../api";
import { ConfirmDialog } from "../../ui";
import { SettingsCard, SettingsRow } from "../settings/primitives";

/**
 * Connector-level disconnect.
 *
 * It lives in its own file because the bespoke detail pages each build their own header,
 * and two of them shipped without any disconnect at all: GitHub offered only "disconnect
 * installation" and Notion only "disconnect account". Neither sub-entity exists for a
 * connector signed in through MCP — that flow writes tokens and a server entry, and no
 * installations or account rows — so those pages showed a connected card with no way
 * back out. The backend always handled it (`disconnect_connector` clears the MCP tokens,
 * the DCR registration and the seeded server); only the button was missing.
 *
 * Shared rather than copied so the next bespoke page cannot drift the same way. It asks first
 * (2026-10-10): one click used to sign everything out with nothing to say it was about to.
 */
export function DisconnectConnector({
  c,
  onChanged,
  onGone,
}: {
  c: Connector;
  onChanged: () => void;
  onGone?: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!c.connected || c.auth === "none") return null;
  return (
    <>
      <button
        type="button"
        className="settings-text-btn danger"
        data-testid={`disconnect-connector-${c.name}`}
        disabled={busy}
        onClick={() => setAsking(true)}
      >
        {busy ? "Disconnecting…" : "Disconnect"}
      </button>
      {asking ? (
        <ConfirmDialog
          title={`Disconnect ${c.title}?`}
          body={`Every ${c.title} account is signed out, and what Reeve saved for it on this computer is removed. Conversations stop using its tools. You can connect it again later.`}
          confirmLabel="Disconnect"
          busyLabel="Disconnecting…"
          onCancel={() => setAsking(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await disconnectConnector(c.name);
              setAsking(false);
              onChanged();
              onGone?.();
            } finally {
              setBusy(false);
            }
          }}
        />
      ) : null}
    </>
  );
}

/** The last card of a connected connector's page: disconnecting it, on a row of its own, the
 * way Settings ▸ Models ends a provider's page with Remove key. */
export function DisconnectRow({ c, onChanged, onGone }: { c: Connector; onChanged: () => void; onGone?: () => void }) {
  if (!c.connected || c.auth === "none") return null;
  return (
    <SettingsCard>
      <SettingsRow title={`Disconnect ${c.title}`} detail="Signs out and removes what Reeve saved for it on this computer.">
        <DisconnectConnector c={c} onChanged={onChanged} onGone={onGone} />
      </SettingsRow>
    </SettingsCard>
  );
}
