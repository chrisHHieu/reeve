import { disconnectAccount, setDefaultAccount, type AccountRow } from "../../api";
import { ConnectSetup } from "./ConnectSetup";
import type { DetailProps } from "./ConnectorsSection";
import { AccountsCard, ConnectorHead, accountViews, accountsState } from "./DetailParts";
import { DisconnectRow } from "./DisconnectButton";
import { ToolsDisclosure } from "./ToolsDisclosure";

// The page of any connector with several accounts on the shared account layer (Notion, Trello and
// the others like them). It reads as the Calendar page does: an Accounts card with a Default tag,
// Make default, and a Disconnect that asks first. Add account opens the connect sheet when the
// connector signs in through the browser, and the token form under the card when it does not.

export function AccountsDetail({ c, onChanged }: DetailProps) {
  const accounts = (c.accounts ?? []) as AccountRow[];
  return (
    <div className="connector-page" data-testid="accounts-detail">
      <ConnectorHead c={c} testId="accounts-status" tone={c.connected ? "ok" : "off"} state={accountsState(c.connected, accounts.length, c.mode === "mcp")} />
      <AccountsCard
        c={c}
        accounts={accountViews(accounts)}
        testId="accounts-group"
        idPrefix="account"
        rowTestId={(id) => `account-${id}`}
        onMakeDefault={(id) => setDefaultAccount(c.name, id)}
        onDisconnect={(id) => disconnectAccount(c.name, id)}
        onChanged={onChanged}
        manualAdd={(close) => (
          <section className="settings-card-block connector-manual-add" data-testid="accounts-manual-add">
            <h2 className="settings-card-heading">Add an account</h2>
            <div className="settings-panel">
              <ConnectSetup
                c={c}
                onConnected={() => {
                  close();
                  onChanged();
                }}
              />
            </div>
          </section>
        )}
      />
      <div>
        <ToolsDisclosure c={c} onChanged={onChanged} />
        <p className="connector-foot">Every account is kept apart: tool results and approvals say which one they used.</p>
      </div>
      <DisconnectRow c={c} onChanged={onChanged} />
    </div>
  );
}
