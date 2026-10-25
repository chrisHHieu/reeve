import { useState } from "react";
import { disconnectGmailAccount, setGmailDefaultAccount, setGmailFilters, type GmailAccount } from "../../api";
import { Icon } from "../Icon";
import { SettingsCard } from "../settings/primitives";
import type { DetailProps } from "./ConnectorsSection";
import { AccountsCard, ConnectorHead, accountViews, accountsState } from "./DetailParts";
import { DisconnectRow } from "./DisconnectButton";
import { ToolsDisclosure } from "./ToolsDisclosure";

// Gmail's page: the mailboxes connected (any number, one the Default, each
// disconnected on its own) and the "Never show agents" filters. A mailbox is added through the
// connect sheet (2026-10-10): the button used to start Google's sign-in itself and say "Check your
// browser…" for 2.5 s whatever happened.

export function GmailDetail({ c, onChanged }: DetailProps) {
  const accounts = (c.accounts ?? []) as GmailAccount[]; // keyed by email, the shape from before the shared account layer
  return (
    <div className="connector-page" data-testid="gmail-detail">
      <ConnectorHead c={c} title="Gmail" testId="gmail-status" tone={c.connected ? "ok" : "off"} state={accountsState(c.connected, accounts.length)} />
      <AccountsCard
        c={c}
        accounts={accountViews(accounts)}
        testId="gmail-accounts"
        idPrefix="gmail"
        rowTestId={(id) => `gmail-account-${id}`}
        onMakeDefault={setGmailDefaultAccount}
        onDisconnect={disconnectGmailAccount}
        onChanged={onChanged}
        addDisabledReason={c.one_click ? undefined : "This build has no Google sign-in configured"}
      />
      <Filters c={c} onChanged={onChanged} />
      <div>
        <ToolsDisclosure c={c} onChanged={onChanged} />
        <p className="connector-foot">
          The filters are applied on this computer, before an agent sees any result. How many were held back shows on
          the tool card and in Activity; what they said never does.
        </p>
      </div>
      <DisconnectRow c={c} onChanged={onChanged} />
    </div>
  );
}

function Filters({ c, onChanged }: Pick<DetailProps, "c" | "onChanged">) {
  const { senders, labels } = c.filters ?? { senders: [], labels: [] };
  const save = async (change: { senders?: string[]; labels?: string[] }) => {
    await setGmailFilters(change);
    onChanged();
  };
  return (
    <div>
      <SettingsCard title="Never show agents" testId="gmail-filters">
        <ChipListRow label="Senders" testid="gmail-filter-senders" placeholder="name@example.com or @domain.com" values={senders} onSave={(next) => save({ senders: next })} />
        <ChipListRow label="Labels" testid="gmail-filter-labels" placeholder="Label name, e.g. Personal" values={labels} onSave={(next) => save({ labels: next })} />
      </SettingsCard>
      <p className="connector-foot">Mail that matches is simply not in what agents read, with nothing left behind for them to probe.</p>
    </div>
  );
}

function ChipListRow({ label, testid, placeholder, values, onSave }: { label: string; testid: string; placeholder: string; values: string[]; onSave: (next: string[]) => Promise<void> }) {
  const [typed, setTyped] = useState("");
  const add = async () => {
    const value = typed.trim();
    if (!value) return;
    setTyped("");
    await onSave([...values, value]);
  };
  return (
    <div className="connector-chip-row" data-testid={testid}>
      <span className="connector-chip-label">{label}</span>
      <span className="connector-chip-field">
        {values.map((value) => (
          <span key={value} className="connector-chip">
            {value}
            <button type="button" className="connector-chip-x" aria-label={`Remove ${value}`} onClick={() => onSave(values.filter((kept) => kept !== value))}>
              <Icon name="x" size={11} />
            </button>
          </span>
        ))}
        <input
          className="connector-chip-input"
          aria-label={`Add to ${label}`}
          placeholder={placeholder}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          onBlur={() => typed.trim() && add()}
        />
      </span>
    </div>
  );
}
