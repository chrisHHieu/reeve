import { useEffect, useRef, useState } from "react";
import { getConnectors, getSlackChannels, type RecentChannel } from "../api";

// A channel field with suggestions: the channels seen lately, and, when Slack is connected, the
// workspace's own channels as a name is typed (the desktop resolves the name to an id from its
// cached conversations.list and the address is built from it). An address can always be typed
// (`slack:C0123`) or a channel's copied link pasted. The list is drawn here rather than with a
// <datalist>, which WKWebView (the macOS shell) does not show at all.

interface WorkspaceChannel {
  address: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

const LOOKUP_DELAY_MS = 250;
const LOOKUP_LIMIT = 12;
const OPTION = "block w-full text-left px-3 py-1.5 hover:bg-paper";

/** The text to look a channel up by, or "" when it is an address or a link and not a name. */
function nameTyped(value: string): string {
  const name = value.trim().replace(/^#/, "");
  return name.includes(":") || name.includes("/") ? "" : name;
}

export function ChannelPicker({
  value,
  onChange,
  recent,
  onSubmit,
  onPickName,
}: {
  value: string;
  onChange: (v: string) => void;
  recent: RecentChannel[];
  onSubmit?: () => void;
  // Told the channel's name when a pick gives one, so the caller can say it wherever it shows the
  // target (the consent line, the summaries).
  onPickName?: (address: string, name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);

  // Whether Slack is connected, asked the first time the list opens (null: not asked yet).
  const [slack, setSlack] = useState<boolean | null>(null);
  useEffect(() => {
    if (!open || slack !== null) return;
    getConnectors()
      .then((connectors) => setSlack(!!connectors.find((c) => c.name === "slack")?.connected))
      .catch(() => setSlack(false));
  }, [open, slack]);

  // The workspace's channels for a typed name, a moment after typing stops. While the lookup runs
  // the list says so: the first one in a workspace pages through every channel, seconds in a big
  // one, and a silent gap read as a typeahead that does not work (owner, 2026-07-09).
  const [workspace, setWorkspace] = useState<WorkspaceChannel[]>([]);
  const [looking, setLooking] = useState(false);
  useEffect(() => {
    const name = nameTyped(value);
    if (!open || !slack || !name) {
      setWorkspace([]);
      setLooking(false);
      return;
    }
    setLooking(true);
    const timer = setTimeout(async () => {
      try {
        const found = await getSlackChannels(name);
        const channels = found.ok ? found.channels || [] : [];
        setWorkspace(channels.slice(0, LOOKUP_LIMIT).map((c) => ({ address: `slack:${c.id}`, name: c.name, is_private: c.is_private, is_member: c.is_member })));
      } catch {
        setWorkspace([]);
      }
      setLooking(false);
    }, LOOKUP_DELAY_MS);
    return () => clearTimeout(timer);
  }, [value, open, slack]);

  // Recent channels narrowed by what is typed (their name, address or last message); all of them
  // when nothing is. Workspace channels the recent list already has are not listed twice.
  const typed = value.trim().toLowerCase();
  const recentShown = recent.filter((c) => !typed || [c.channel, c.name || "", c.last_text || ""].some((text) => text.toLowerCase().includes(typed)));
  const listed = new Set(recentShown.map((c) => c.channel));
  const workspaceShown = workspace.filter((c) => !listed.has(c.address));

  // The value is always the address, but the field shows the channel's name when one is known (a
  // pick, the recent list, the workspace lookup), and the address again while it is being edited;
  // the tooltip always has it (owner, 2026-07-11: the field showed `slack:T…/C…`).
  const [editing, setEditing] = useState(false);
  const [namesPicked, setNamesPicked] = useState<Record<string, string>>({});
  const knownName = namesPicked[value] || recent.find((c) => c.channel === value)?.name || workspace.find((c) => c.address === value)?.name || "";

  // A pick happens on mousedown, before the field's blur would close the list; it is then final,
  // so the field is blurred and shows the name.
  const pick = (event: React.MouseEvent, address: string, name?: string) => {
    event.preventDefault();
    onChange(address);
    if (name) {
      setNamesPicked((was) => ({ ...was, [address]: name }));
      onPickName?.(address, name);
    }
    setOpen(false);
    field.current?.blur();
  };

  return (
    <div className="relative flex-1 min-w-0" ref={box}>
      <input
        ref={field}
        className="chan-input w-full"
        placeholder="slack:C0123 or channel link"
        value={!editing && knownName ? `#${knownName}` : value}
        title={value || undefined}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setEditing(true);
          setOpen(true);
        }}
        onBlur={() => setEditing(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter" && onSubmit) {
            setOpen(false);
            onSubmit();
          }
        }}
      />
      {open && (recentShown.length > 0 || workspaceShown.length > 0 || looking) && (
        <div className="absolute left-0 right-0 top-full mt-1 z-40 rounded-xl border border-line bg-panel shadow-lg py-1 max-h-56 overflow-y-auto" role="listbox" data-testid="channel-suggestions">
          {recentShown.map((c) => (
            <button key={c.channel} role="option" className={OPTION} onMouseDown={(e) => pick(e, c.channel, c.name || undefined)}>
              <span className="text-[13px] text-ink">{c.name ? `#${c.name}` : c.channel}</span>
              {c.name && <span className="ml-1.5 text-[11px] text-faint">{c.channel}</span>}
              {c.last_text && (
                <span className="block text-[11px] text-faint truncate">
                  {c.last_from ? `${c.last_from}: ` : ""}
                  {c.last_text}
                </span>
              )}
            </button>
          ))}
          {looking && workspaceShown.length === 0 && (
            <div className="px-3 py-1.5 text-[12px] text-faint" data-testid="roster-searching">
              searching your workspace’s channels…
            </div>
          )}
          {workspaceShown.map((c) => (
            <button key={c.address} role="option" className={OPTION} data-testid={`roster-channel-${c.address}`} onMouseDown={(e) => pick(e, c.address, c.name)}>
              <span className="text-[13px] text-ink">
                {c.is_private ? "🔒 " : "#"}
                {c.name}
              </span>
              {!c.is_member && <span className="block text-[11px] text-warnInk">invite @reeve to this channel in Slack so it can listen</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
