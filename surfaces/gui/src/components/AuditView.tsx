import { useEffect, useState } from "react";
import { getAudit, type AuditEvent } from "../api";
import { PageHeader, PageShell } from "../ui";

// Activity: what connectors and tools did, newest first, on the same page shell as Integrations.
// It only reads. The arguments shown were cleaned of secrets before they were stored.
const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD = "px-3 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const FILTERS = [
  { key: "session_id", label: "Filter by session id", placeholder: "session id" },
  { key: "connector", label: "Filter by connector", placeholder: "connector" },
  { key: "tool", label: "Filter by tool", placeholder: "tool" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

export function AuditView() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filters, setFilters] = useState<Record<FilterKey, string>>({ session_id: "", connector: "", tool: "" });

  const load = () => {
    const given = (key: FilterKey) => filters[key].trim() || undefined;
    return getAudit({ limit: 150, session_id: given("session_id"), connector: given("connector"), tool: given("tool") })
      .then(setEvents)
      .catch(() => setEvents([]));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => void load(), []);

  return (
    <PageShell>
      <PageHeader title="Activity" sub="What connectors and tools did lately. Arguments are cleaned of secrets before they are stored." />
      <div className="flex items-center gap-2 flex-wrap mb-4">
        {FILTERS.map(({ key, label, placeholder }) => (
          <input
            key={key}
            className={FIELD}
            aria-label={label}
            placeholder={placeholder}
            value={filters[key]}
            onChange={(e) => setFilters((was) => ({ ...was, [key]: e.target.value }))}
          />
        ))}
        <button className="text-[13px] px-3 py-1.5 rounded-lg bg-accent text-white shrink-0" onClick={load}>
          Filter
        </button>
      </div>
      {events.length ? (
        <div className="space-y-2">
          {events.map((event) => (
            <AuditRow event={event} key={event.id} />
          ))}
        </div>
      ) : (
        <div className={CARD + " p-4 text-[13px] text-muted"}>No audit events yet.</div>
      )}
    </PageShell>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  const details = [event.approval, event.status].filter(Boolean).map((part) => ` · ${part}`);
  const args = event.args && Object.keys(event.args).length ? formatArguments(event.args) : "";
  const note = event.reason || event.result_preview;
  return (
    <div className={CARD + " p-3.5"}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-[13px] font-medium text-ink">{event.tool}</span>
        <span className="text-[12px] text-faint">
          {event.connector || "tool"} · {event.stage || event.status || "event"} · {event.timestamp}
        </span>
      </div>
      <div className="text-[12px] text-muted mt-0.5">
        session {event.session_id || "-"}
        {details.join("")}
      </div>
      {event.resource && <div className="text-[12px] text-faint mt-0.5">resource: {event.resource}</div>}
      {args && <div className="font-mono text-[12px] text-muted mt-1.5 break-words">{args}</div>}
      {note && <div className="text-[12px] text-faint mt-1">{note}</div>}
    </div>
  );
}

/** `key=value` pairs, two spaces apart; a value that is not text is shown as JSON. */
function formatArguments(args: Record<string, any>) {
  return Object.entries(args)
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join("  ");
}
