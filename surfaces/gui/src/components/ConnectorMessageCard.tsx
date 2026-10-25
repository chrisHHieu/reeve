// A message that came in through a connector, drawn as a card: a header tinted in the
// connector's colour (its badge, the channel and the sender, how long ago, "via <connector>") over
// the message as it was written, with the colour down the left edge. It only draws; every
// connector gets the same card from the registry, Slack included.
//
// The message's `source` names its connector by id and carries no visuals. The logo and the
// label come from the registry by that id (a plug for an id it does not know). The colour is not in
// the source either: a caller that has the connector list passes the descriptor's `brand_color` as
// `brandColor`, and without one the registry's neutral grey is used.
//
// Hovering or focusing the header shows `channel_id · sender_id` in place of the names. React state
// does it rather than CSS :hover, so it works from the keyboard and a test can see it; the ids are
// in the header's `title` as well.

import { useState, type CSSProperties } from "react";
import type { MessageSource } from "../api";
import { sinceLabel } from "../ago";
import { ConnectorBadge, hexToRgba, NEUTRAL } from "../connectors/ConnectorIcon";
import { resolveConnector } from "../connectors/registry";

/** The clock time for the time element's tooltip, e.g. "2:14 PM". */
function clockTime(tsSeconds: number): string {
  if (!tsSeconds || !isFinite(tsSeconds)) return "";
  return new Date(tsSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function ConnectorMessageCard({ source, brandColor }: { source: MessageSource; brandColor?: string }) {
  const [showIds, setShowIds] = useState(false);
  const { key, entry } = resolveConnector(source.connector);
  const color = (brandColor || "").trim() || NEUTRAL;
  // Only the two brand properties on the article, not `color`, so the message keeps the normal
  // text colour; the header and the channel name read `var(--brand)`.
  const brand = { ["--brand"]: color, ["--brand-soft"]: hexToRgba(color, 0.12) || "var(--line)" } as CSSProperties;
  const ids = `${source.channel_id} · ${source.sender_id}`;
  const when = sinceLabel(source.ts, { justNow: 45, hoursUntilDays: 24, dateFromDays: 7 }) || "";

  return (
    <article className="connector-card rounded-xl2 border border-line bg-panel overflow-hidden" data-brand={key} style={brand}>
      <header
        className="connector-card-head flex items-center gap-2 px-3.5 py-2 border-b border-line outline-none"
        tabIndex={0}
        title={ids}
        style={{ background: "var(--brand-soft)" }}
        onMouseEnter={() => setShowIds(true)}
        onMouseLeave={() => setShowIds(false)}
        onFocus={() => setShowIds(true)}
        onBlur={() => setShowIds(false)}
      >
        <ConnectorBadge connector={{ logo: source.connector, brand_color: color }} size={20} title={entry.label} />
        {showIds ? (
          <span className="font-mono text-[12px] text-faint">{ids}</span>
        ) : (
          <>
            <span className="text-[13px] font-semibold" style={{ color: "var(--brand)" }}>
              {source.channel_name}
            </span>
            <span className="text-faint">·</span>
            <span className="text-[13px] font-medium">{source.sender_name}</span>
            <span className="text-[11px] text-faint ml-0.5">via {entry.label}</span>
          </>
        )}
        <time className="ml-auto text-[11px] text-faint whitespace-nowrap" title={clockTime(source.ts)}>
          {when}
        </time>
      </header>
      <div className="px-3.5 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap">{source.text}</div>
    </article>
  );
}
