// What the search card and the fetch card both draw: a link that can only be the web, and a
// site's mark.

import { useEffect, useState } from "react";
import { fetch, httpBase } from "../api/base";
import { hostOf } from "./webSearchView";

/** Only these schemes become links. Everything else renders as text.
 *
 *  A card's links are built from a third party's data, and must not be a way to hand the OS
 *  an arbitrary scheme (`mailto:`, which the markdown renderer allows, or worse); the cost is
 *  that an exotic-but-legitimate URL is not clickable, which is the right way round. */
export function safeHref(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? url : undefined;
  } catch {
    return undefined;
  }
}

/** A stable tile color per host, so the same site is the same color every time. */
const FAV_COLORS = ["#3b5cf6", "#cc785c", "#0f9d58", "#a855f7", "#0f766e", "#b45309", "#be123c", "#0369a1"];

function favColor(host: string): string {
  let h = 0;
  for (let i = 0; i < host.length; i++) h = (h * 31 + host.charCodeAt(i)) >>> 0;
  return FAV_COLORS[h % FAV_COLORS.length];
}

/** One request per host for the whole app: every card showing the site shares the answer. */
const kept = new Map<string, Promise<string | null>>();

function keptIcon(host: string): Promise<string | null> {
  let icon = kept.get(host);
  if (!icon) {
    icon = fetch(`${httpBase()}/v1/favicons/${encodeURIComponent(host)}`)
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => (blob ? URL.createObjectURL(blob) : null))
      .catch(() => null)
      .then((url) => {
        // Only an icon is remembered: a miss (the server restarting, a 404) is asked again the
        // next time a card shows the site, or every card would keep its letter until a reload.
        if (!url) kept.delete(host);
        return url;
      });
    kept.set(host, icon);
  }
  return icon;
}

/** The site mark. The site's own icon when the search that found it kept one (`icon`): the
 *  server fetched it inside that allowed search and serves it from this computer, so the GUI
 *  never calls the site. Otherwise, or until it arrives, its first letter on a
 *  stable tint. */
export function SourceFav({ url, icon }: { url: string; icon?: boolean }) {
  const host = hostOf(url).replace(/^www\./, "");
  const letter = (host.match(/[a-z0-9]/i)?.[0] ?? "•").toUpperCase();
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!icon || !host) return;
    let live = true;
    void keptIcon(host).then((found) => {
      if (live) setSrc(found);
    });
    return () => {
      live = false;
    };
  }, [icon, host]);
  if (src) {
    return <img className="web-fav web-fav-img" src={src} alt="" aria-hidden onError={() => setSrc(null)} />;
  }
  return (
    <span className="web-fav" style={{ background: favColor(host) }} aria-hidden>
      {letter}
    </span>
  );
}
