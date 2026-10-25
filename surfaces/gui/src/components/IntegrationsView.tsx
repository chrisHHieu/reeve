import { useEffect, useState } from "react";
import { getConnectors } from "../api";
import { PageHeader, PageShell } from "../ui";
import { ConnectorsSection } from "./connectors/ConnectorsSection";
import { Icon } from "./Icon";

// The Connectors page. Custom MCP servers are a group on it, with their own Add button, so its side
// navigation has one entry, which carries how many connectors there are. A conversation's channels
// are on Slack's page and in the conversation's own chip; the activity log is in Settings.

const RECOUNT_MS = 5000;

export function IntegrationsView() {
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    const recount = () =>
      getConnectors()
        .then((list) => setCount(list.length))
        .catch(() => {});
    recount();
    const timer = setInterval(recount, RECOUNT_MS);
    return () => clearInterval(timer);
  }, []);

  const nav = (
    <nav className="page-subnav w-[208px] shrink-0 border-r border-line bg-panel/40 px-3 py-4">
      <div className="px-2 text-[13px] font-semibold mb-3 flex items-center gap-2">
        <Icon name="plug" size={16} /> Connectors
      </div>
      <button className="w-full text-left px-2.5 py-2 rounded-lg text-[13px] flex items-center justify-between bg-paper text-accent font-medium">
        <span className="flex items-center gap-2 min-w-0">
          <Icon name="plug" size={15} /> Connectors
        </span>
        {count != null && <span className="text-[11px] shrink-0 text-accent">{count}</span>}
      </button>
    </nav>
  );

  return (
    <PageShell nav={nav}>
      <section>
        <PageHeader title="Connectors" sub="Apps and tools Reeve can use. Connected ones come first." />
        <ConnectorsSection />
      </section>
    </PageShell>
  );
}
