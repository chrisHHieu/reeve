import { useEffect, useState, type ReactNode } from "react";
import { allowUser, disallowUser, getSubscriptions, resolveUnauthorized, unsubscribeChannel, type Connector, type ParkedMessage, type Subscription } from "../../api";
import { GRP, PILL_ACCENT, PILL_LINE, ROW, XBTN } from "./ui";

// The rows of a connector that brings messages in, on its page's group card: People (who may write
// to the agent), Waiting (a message from someone not allowed yet, kept rather than dropped, so a
// first contact is answered by allowing it), and Listening (which conversation follows which
// channel). Slack's page and the page of any other two-way connector draw the same rows.

const LABEL = "text-[13px] text-muted w-24 shrink-0";

/** Two letters for a person's chip: first and last name's initials, or a single name's first two. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[words.length - 1][0]).toUpperCase();
}

export function Avatar({ name, quiet }: { name: string; quiet?: boolean }) {
  const look = quiet ? "bg-paper border border-line text-muted" : "bg-accentSoft text-accent";
  return <span className={`w-5 h-5 rounded-full ${look} grid place-items-center text-[9px] font-bold shrink-0`}>{initials(name)}</span>;
}

/** The people allowed to write, each a chip with a ×; `children` is how someone is added. */
export function PeopleRow({
  allowed,
  names,
  empty,
  onRemove,
  children,
}: {
  allowed: string[];
  names?: Record<string, string | null>;
  empty: string;
  onRemove: (userId: string) => void;
  children?: ReactNode;
}) {
  const nameOf = (id: string) => names?.[id] || id;
  return (
    <div className={ROW}>
      <span className={LABEL}>People</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {allowed.length === 0 && <span className="text-[12px] text-faint">{empty}</span>}
        {allowed.map((id) => (
          <span key={id} className="inline-flex items-center gap-1.5 pl-1 pr-2 py-0.5 rounded-full bg-paper border border-line text-[13px]" title={`id ${id}`}>
            <Avatar name={nameOf(id)} />
            {nameOf(id)}
            <button className={XBTN} title="remove" onClick={() => onRemove(id)}>
              ×
            </button>
          </span>
        ))}
        {children}
      </span>
    </div>
  );
}

export function WaitingRow({ connector, message, onChanged }: { connector: string; message: ParkedMessage; onChanged: () => void }) {
  const answer = async (action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized(connector, message.id, action);
    onChanged();
  };
  return (
    <div className={ROW + " bg-warnSoft/25"} data-testid={`waiting-${message.id}`}>
      <span className={LABEL}>Waiting</span>
      <span className="min-w-0 flex-1">
        <span className="font-medium text-[13px]">{message.user_name || message.user_id}</span> <span className="text-[13px] text-muted">in {message.chat_name || message.chat_id}</span>
        <span className="block text-[13px] text-muted truncate">“{message.text}”</span>
      </span>
      <button className={PILL_ACCENT + " !py-1"} data-testid={`parked-allow-deliver-${message.id}`} title="Allow the sender and deliver this message now" onClick={() => answer("allow_deliver")}>
        Allow & deliver
      </button>
      <button className={PILL_LINE + " !py-1"} data-testid={`parked-allow-${message.id}`} title="Allow the sender; this message is discarded" onClick={() => answer("allow")}>
        Allow
      </button>
      <button className={XBTN + " px-1"} data-testid={`parked-dismiss-${message.id}`} title="Dismiss" onClick={() => answer("dismiss")}>
        ×
      </button>
    </div>
  );
}

/** The conversations following one of the connector's channels; none, no row. A conversation
 *  subscribes from its own Sources ▸ Channels; here it can only be stopped. */
export function ListeningRows({ connector, subscriptions, onChanged }: { connector: string; subscriptions: Subscription[]; onChanged: () => void }) {
  if (!subscriptions.length) return null;
  const stop = async (s: Subscription) => {
    await unsubscribeChannel(s.session_id, s.channel);
    onChanged();
  };
  return (
    <div className={ROW} data-testid={`listening-${connector}`}>
      <span className={LABEL}>Listening</span>
      <span className="min-w-0 flex-1 space-y-1">
        {subscriptions.map((s) => (
          <span key={s.session_id + s.channel} className="flex items-center gap-2 text-[13px]">
            <span className="font-medium truncate" title={s.session_id}>
              {s.session_title || s.session_id}
            </span>
            <span className="text-faint">←</span>
            <span className="text-muted truncate" title={s.channel}>
              {s.channel_name ? `#${s.channel_name}` : s.channel}
            </span>
            <button className={XBTN + " ml-auto"} title="Unsubscribe this session" onClick={() => stop(s)}>
              ×
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}

/** A channel id's platform: "telegram:123" is Telegram's; a bare id is Slack's, from before ids
 *  carried their platform. */
export const platformOf = (channel: string) => (channel.includes(":") ? channel.split(":")[0] : "slack");

/** People who wrote to the bot and are not allowed yet, each one Allow away. */
function RecentRow({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const strangers = (c.recent ?? []).filter((sender) => !sender.authorized);
  if (!strangers.length) return null;
  return (
    <div className={ROW}>
      <span className={LABEL}>Recent</span>
      <span className="min-w-0 flex-1 space-y-1.5">
        {strangers.map((sender) => (
          <span key={sender.user_id} className="flex items-center gap-2 text-[13px]">
            <Avatar name={sender.user_name || "?"} quiet />
            <span className="min-w-0 truncate" title={`id ${sender.user_id}`}>
              {sender.user_name || "unknown"} <span className="text-faint">· {sender.chat_type}</span>
            </span>
            <button className={PILL_ACCENT + " !py-1 ml-auto"} data-testid={`recent-allow-${sender.user_id}`} onClick={() => allowUser(c.name, sender.user_id).then(onChanged)}>
              Allow
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}

/** The group card of a two-way connector with no page of its own (Telegram, Discord): who may
 *  write, who wrote, what waits, and, on a chat platform, what listens. */
export function TwoWayGroup({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const [listening, setListening] = useState<Subscription[]>([]);
  const load = () =>
    getSubscriptions()
      .then(setListening)
      .catch(() => setListening([]));
  useEffect(() => {
    if (c.channels) load();
  }, [c.name, c.channels]);
  const changed = () => {
    onChanged();
    if (c.channels) load();
  };
  return (
    <div className={GRP}>
      <PeopleRow
        allowed={c.allowed_users}
        names={c.allowed_user_names}
        empty="nobody yet — message the bot once, then allow yourself here"
        onRemove={(id) => disallowUser(c.name, id).then(changed)}
      />
      <RecentRow c={c} onChanged={changed} />
      {(c.unauthorized ?? []).map((message) => (
        <WaitingRow key={message.id} connector={c.name} message={message} onChanged={changed} />
      ))}
      {c.channels && <ListeningRows connector={c.name} subscriptions={listening.filter((s) => platformOf(s.channel) === c.name)} onChanged={changed} />}
    </div>
  );
}
