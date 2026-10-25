import { disconnectGcalAccount, setGcalDefaultAccount, type GmailAccount } from "../../api";
import type { DetailProps } from "./ConnectorsSection";
import { AccountsCard, ConnectorHead, accountViews, accountsState } from "./DetailParts";
import { DisconnectRow } from "./DisconnectButton";
import { ToolsDisclosure } from "./ToolsDisclosure";

// Google Calendar's page: Gmail's, without the filters. Any number of accounts, one the Default,
// each disconnected on its own; one is added through the connect sheet, as every connection is.

export function CalendarDetail({ c, onChanged }: DetailProps) {
  const accounts = (c.accounts ?? []) as GmailAccount[]; // keyed by email, the shape from before the shared account layer
  return (
    <div className="connector-page" data-testid="gcal-detail">
      <ConnectorHead c={c} title="Google Calendar" testId="gcal-status" tone={c.connected ? "ok" : "off"} state={accountsState(c.connected, accounts.length)} />
      <AccountsCard
        c={c}
        accounts={accountViews(accounts)}
        testId="gcal-accounts"
        idPrefix="gcal"
        rowTestId={(id) => `gcal-account-${id}`}
        onMakeDefault={setGcalDefaultAccount}
        onDisconnect={disconnectGcalAccount}
        onChanged={onChanged}
        addDisabledReason={c.one_click ? undefined : "This build has no Google sign-in configured"}
      />
      <div>
        <ToolsDisclosure c={c} onChanged={onChanged} />
        <p className="connector-foot">Adding, changing or deleting an event always waits for your approval, and the approval names the account.</p>
      </div>
      <DisconnectRow c={c} onChanged={onChanged} />
    </div>
  );
}
