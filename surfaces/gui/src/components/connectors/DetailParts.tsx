import { useState, type ReactNode } from "react";
import type { Connector } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { ConfirmDialog } from "../../ui";
import { Icon } from "../Icon";
import { FoldBody, SettingsCard, SettingsRow, TextButton } from "../settings/primitives";
import { AddConnectionModal } from "./AddConnectionModal";
import { accountKey } from "./McpOneClick";

// The parts every connector page is built from (2026-10-10), so Gmail, Calendar, Notion and the
// generic page read as one family with Settings ▸ Models: the head (logo, name, state), an
// Accounts card whose Add goes through the connect sheet and whose rows each say how to remove
// them, then the page's own cards, Tools, and Disconnect on a row of its own at the end.

/** A connector page's head: logo, name, and one line of state with its dot. */
export function ConnectorHead({
  c,
  title,
  state,
  tone = "ok",
  testId,
  action,
}: {
  c: Connector;
  title?: string;
  state: ReactNode;
  tone?: "ok" | "off" | "warn";
  testId?: string;
  action?: ReactNode;
}) {
  return (
    <header className="settings-provider-head connector-head">
      <ConnectorBadge connector={c} size={44} title={title ?? c.title} />
      <div className="settings-provider-head-text">
        <h2 className="settings-provider-title">{title ?? c.title}</h2>
        <div className={"connector-state " + tone} data-testid={testId}>
          <span className="connector-state-dot" aria-hidden="true" />
          {state}
        </div>
      </div>
      {action ? <div className="connector-head-action">{action}</div> : null}
    </header>
  );
}

export type AccountView = {
  /** What the account is keyed by (an email, or an account id). */
  id: string;
  name: string;
  /** A second line when the id says more than the name. */
  sub?: string;
  isDefault: boolean;
  needsReauth?: boolean;
};

/**
 * A multi-account connector's accounts. Each row says which is the default, offers Make default,
 * and has a Disconnect that asks first (it was a faint × that removed the account on the first
 * click, and the owner could not find it). Add opens the connect sheet in its add-an-account
 * shape, so the wait, a failure and the new account's name are all said.
 */
export function AccountsCard({
  c,
  accounts,
  testId,
  idPrefix,
  rowTestId,
  onMakeDefault,
  onDisconnect,
  onChanged,
  addDisabledReason,
  manualAdd,
}: {
  c: Connector;
  accounts: AccountView[];
  testId: string;
  /** The test-id prefix of a row's buttons ("gmail" → gmail-make-default-…, gmail-disconnect-…). */
  idPrefix: string;
  /** The test id of one row. */
  rowTestId: (id: string) => string;
  onMakeDefault: (id: string) => Promise<unknown>;
  onDisconnect: (id: string) => Promise<unknown>;
  onChanged: () => void;
  /** Why Add cannot run here (no sign-in in this build); the button is off and says so. */
  addDisabledReason?: string;
  /** No browser sign-in: Add opens this form instead of the sheet. */
  manualAdd?: (close: () => void) => ReactNode;
}) {
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<AccountView | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  return (
    <>
      <SettingsCard
        title="Accounts"
        testId={testId}
        action={
          <button
            type="button"
            className="settings-text-btn"
            data-testid="add-account-btn"
            disabled={!!addDisabledReason}
            title={addDisabledReason}
            onClick={() => (manualAdd && !c.one_click ? setManualOpen(!manualOpen) : setAdding(true))}
          >
            <Icon name="plus" size={13} /> Add account
          </button>
        }
      >
        {accounts.length === 0 ? (
          <div className="settings-empty">No accounts yet. Add one to let Reeve use {c.title}.</div>
        ) : (
          accounts.map((a) => (
            <SettingsRow
              key={a.id}
              testId={rowTestId(a.id)}
              title={a.name}
              detail={
                <span className="connector-account-tags">
                  {a.isDefault ? <span className="settings-tag connector-default">Default</span> : null}
                  {a.needsReauth ? <span className="settings-tag settings-tag-warn">Sign in again</span> : null}
                  {a.sub ? <span className="connector-account-sub" title={a.sub}>{a.sub}</span> : null}
                </span>
              }
            >
              {!a.isDefault ? (
                <TextButton
                  testId={`${idPrefix}-make-default-${a.id}`}
                  onClick={() => void onMakeDefault(a.id).then(onChanged)}
                >
                  Make default
                </TextButton>
              ) : null}
              <TextButton tone="danger" testId={`${idPrefix}-disconnect-${a.id}`} onClick={() => setRemoving(a)}>
                Disconnect
              </TextButton>
            </SettingsRow>
          ))
        )}
      </SettingsCard>

      {/* Only a connector with no browser sign-in adds by hand; one that has one goes through
          the sheet, and a hidden form beside it would be a second Connect on the page. */}
      {manualAdd && !c.one_click ? <FoldBody open={manualOpen}>{manualAdd(() => setManualOpen(false))}</FoldBody> : null}

      {adding ? (
        <AddConnectionModal
          c={c}
          addAccount={accounts.map((a) => a.id)}
          onClose={() => setAdding(false)}
          onChanged={onChanged}
        />
      ) : null}
      {removing ? (
        <ConfirmDialog
          title={`Disconnect ${removing.name}?`}
          body={
            accounts.length > 1
              ? `Reeve stops using this ${c.title} account.${removing.isDefault ? " Another account becomes the default." : ""} The other accounts stay connected.`
              : `This is the only ${c.title} account, so ${c.title} stops working in conversations until one is added again.`
          }
          confirmLabel="Disconnect"
          busyLabel="Disconnecting…"
          onCancel={() => setRemoving(null)}
          onConfirm={async () => {
            await onDisconnect(removing.id);
            setRemoving(null);
            onChanged();
          }}
        />
      ) : null}
    </>
  );
}

/** Rows of either account shape, as the card shows them. */
export function accountViews(
  rows: { email?: string; account_id?: string; name?: string; default?: boolean; needs_reauth?: boolean }[],
): AccountView[] {
  return rows.map((a) => {
    const { id, name } = accountKey(a);
    return {
      id,
      name,
      sub: a.name && a.account_id && a.name !== a.account_id ? a.account_id : undefined,
      isDefault: !!a.default,
      needsReauth: !!a.needs_reauth,
    };
  });
}

/** The head's state for a connector with accounts: "Not connected", or how many there are. A
 *  connection made by an MCP sign-in has no account rows (the server entry is the one connection),
 *  so counting them would say "0 accounts" of a connector that works; `viaSignIn` says so instead. */
export function accountsState(connected: boolean, count: number, viaSignIn = false): string {
  if (!connected) return "Not connected";
  if (viaSignIn) return "Connected · browser sign-in";
  return `${count} account${count === 1 ? "" : "s"}`;
}
