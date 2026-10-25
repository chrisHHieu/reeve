import { useEffect, useRef, useState } from "react";
import { allowUser, disallowUser, getSlackDirectory, getSubscriptions, type SlackMember, type Subscription } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import type { DetailProps } from "./ConnectorsSection";
import { DisconnectConnector } from "./DisconnectButton";
import { SlackUserToken } from "./SlackUserToken";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { ListeningRows, PeopleRow, platformOf, WaitingRow } from "./TwoWayRows";
import { FOOT, GRP, GRP_H, ROW } from "./ui";

// Slack's page. Slack ids belong to one workspace, so everything is under the
// workspace it is in: People (who may write to the agent), Waiting (messages from people not yet
// allowed), Listening (which conversation follows which channel), and Disconnect. A workspace is
// added one way only, the header's button and its sheet (One click | Manual).

export function SlackDetail({ c, onChanged }: DetailProps) {
  const [listening, setListening] = useState<Subscription[]>([]);
  const loadListening = () =>
    getSubscriptions()
      .then(setListening)
      .catch(() => setListening([]));
  useEffect(() => {
    loadListening();
  }, [c.name]);
  const changed = () => {
    onChanged();
    loadListening();
  };

  return (
    <div data-testid="slack-page">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="Slack" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">Slack</h2>
          <div className="text-[13px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="slack-mode-badge">Connected · Socket Mode</span>
              </>
            ) : (
              <span>Not connected</span>
            )}
          </div>
        </div>
        <DisconnectConnector c={c} onChanged={changed} />
      </div>

      {c.connected ? (
        // Socket Mode is one workspace with one list of people.
        <div data-testid="slack-manual-card">
          <div className={GRP_H}>{c.account || "workspace"}</div>
          <div className={GRP}>
            <PeopleRow
              allowed={c.allowed_users}
              names={c.allowed_user_names}
              empty="nobody yet — pick a name, or approve a waiting sender below"
              onRemove={(id) => disallowUser("slack", id).then(changed)}
            >
              <PersonPicker allowed={c.allowed_users} onChanged={changed} />
            </PeopleRow>
            {(c.unauthorized ?? []).map((message) => (
              <WaitingRow key={message.id} connector="slack" message={message} onChanged={changed} />
            ))}
            <ListeningRows connector="slack" subscriptions={listening.filter((s) => platformOf(s.channel) === "slack")} onChanged={changed} />
          </div>
        </div>
      ) : (
        <div className={GRP}>
          <div className={ROW + " text-[13px] text-muted"}>Connects through a Slack app of your own: a bot token and a Socket Mode app token.</div>
        </div>
      )}

      <SlackUserToken c={c} onChanged={changed} />
      <ToolsDisclosure c={c} onChanged={onChanged} />
      {c.connected && <div className={FOOT + " mt-2"}>Names come from Slack automatically. IDs show on hover.</div>}
    </div>
  );
}

// How tall the picker can get: the search field, the list at its max-h-56, the note under it.
const PICKER_HEIGHT = 300;
const PICKER_WIDTH = 300;

/** Where the picker opens. It is `fixed`, because the group cards hide what overflows them (GRP is
 *  overflow-hidden) and a popover inside one was cut off after the first row. It opens upward when
 *  there is no room below: a fixed box cannot be scrolled to, and with the button low on Settings ▸
 *  Connectors the names opened off the bottom of the window (hermetic slack-directory spec,
 *  2026-09-26). */
function pickerPlace(button: DOMRect): { top?: number; bottom?: number; left: number } {
  const left = Math.min(button.left, window.innerWidth - PICKER_WIDTH);
  const roomBelow = window.innerHeight - button.bottom - 4;
  const below = roomBelow >= PICKER_HEIGHT || button.top < PICKER_HEIGHT;
  return below ? { top: button.bottom + 4, left } : { bottom: window.innerHeight - button.top + 4, left };
}

// Find a person by name in the workspace's directory (users.list, cached on the desktop) and allow
// them, name and all. Approving a waiting sender stays the way in for people nobody added first.
function PersonPicker({ allowed, onChanged }: { allowed: string[]; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [people, setPeople] = useState<SlackMember[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [place, setPlace] = useState<ReturnType<typeof pickerPlace> | null>(null);
  const box = useRef<HTMLSpanElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);

  const toggle = () => {
    if (open) return setOpen(false);
    const rect = button.current?.getBoundingClientRect();
    setPlace(rect ? pickerPlace(rect) : null);
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => {
      getSlackDirectory(query)
        .then((found) => {
          if (!found.ok) return setProblem(found.error || "directory unavailable");
          setPeople(found.members || []);
          setProblem(null);
        })
        .catch(() => setProblem("directory unavailable"));
    }, 200);
    return () => clearTimeout(timer);
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);

  const allow = async (person: SlackMember) => {
    const done = await allowUser("slack", person.id, person.name);
    if (done?.ok === false) return setProblem(done.error || "could not add person");
    setOpen(false);
    setQuery("");
    onChanged();
  };
  const notYetAllowed = people.filter((person) => !allowed.includes(person.id));

  return (
    <span className="relative" ref={box}>
      <button
        ref={button}
        className="inline-flex items-center px-2 py-0.5 rounded-full border border-dashed border-line text-[13px] text-muted hover:text-ink hover:border-faint"
        data-testid="add-person"
        title="Pick from the workspace directory"
        onClick={toggle}
      >
        ＋ Add person
      </button>
      {open && (
        <div className="fixed z-50 w-72 rounded-xl border border-line bg-panel shadow-lg p-1" style={{ top: place?.top, bottom: place?.bottom, left: place?.left }} data-testid="person-picker">
          <input
            autoFocus
            className="w-full bg-paper border border-line rounded-lg px-2 py-1 text-[13px] outline-none placeholder:text-faint"
            placeholder="Type a name…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
          />
          <div className="max-h-56 overflow-y-auto py-1">
            {problem ? (
              <div className="px-2 py-1.5 text-[12px] text-warnInk">{problem}</div>
            ) : notYetAllowed.length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-faint">no matches</div>
            ) : (
              notYetAllowed.map((person) => (
                <button
                  key={person.id}
                  className="block w-full text-left px-2 py-1.5 rounded-lg hover:bg-paper"
                  data-testid={`pick-person-${person.id}`}
                  title={`id ${person.id}`}
                  onMouseDown={(e) => {
                    e.preventDefault(); // on mousedown, so the pick happens before the field's blur
                    allow(person);
                  }}
                >
                  <span className="text-[13px] font-medium">{person.name}</span> <span className="text-[12px] text-faint">@{person.handle}</span>
                  {person.guest && <span className="ml-1.5 text-[11px] text-warnInk bg-warnSoft/70 border border-warnInk/15 rounded px-1 py-0.5">guest</span>}
                </button>
              ))
            )}
          </div>
          <div className="px-2 pb-1 text-[11px] text-faint">From your workspace's directory, kept on this computer.</div>
        </div>
      )}
    </span>
  );
}
