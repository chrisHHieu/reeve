// What every served page needs from the app, whichever view shows it: an address at the
// preview door, the two frames, the page's own reports (where it is, what it tried to reach), and
// the person's answer to "let this page use the internet?". The Files tab's page view and a
// design's tab both draw their own bars around it.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePreviewGrant } from "./usePreviewGrant";

const SANDBOX = "allow-scripts allow-same-origin allow-forms allow-modals";
/** The frame a served page runs in, shared with the variants view. */
export const PAGE_SANDBOX = SANDBOX;

const attr = (value: string) => value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

/** The outer frame's document: ours, static, and the one place `frame-src` can be said. */
export function holderDocument(origin: string, url: string, title: string, internet: boolean): string {
  const frames = internet ? "http: https:" : origin;
  return (
    `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src ${frames}; style-src 'unsafe-inline'">` +
    "<style>html,body,iframe{margin:0;padding:0;width:100%;height:100%;border:0;display:block;background:#fff}</style>" +
    `<iframe src="${attr(url)}" title="${attr(title)}"></iframe>`
  );
}

const decode = (value: string) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};

export function usePageHost(sessionId: string, path: string) {
  const [internet, setInternet] = useState(false);
  const [asking, setAsking] = useState(false);
  const [mounts, setMounts] = useState(0);
  // The page on show inside the folder, as the page itself reports it. Text to show, no more.
  const [page, setPage] = useState("");
  const [blocked, setBlocked] = useState<string[]>([]);
  const here = useRef("");
  const holder = useRef<HTMLIFrameElement | null>(null);
  const grant = usePreviewGrant(sessionId, path, { internet });
  // A view's own handler for what the page says (the design bridge's words); true when taken.
  const onSaid = useRef<(said: Record<string, unknown>) => boolean>(() => false);

  useEffect(() => {
    setInternet(false);
    setAsking(false);
    setPage("");
    setBlocked([]);
    here.current = "";
  }, [sessionId, path]);

  const prefix = grant?.key ? `/s/${grant.key}/` : "";
  const first = grant?.url && grant.origin ? decode(grant.url.slice(grant.origin.length + prefix.length)) : "";

  // Listening before the frame can speak: a layout effect runs with the commit that puts the
  // frame on the page, so the page's first word (where it is) is never missed.
  useLayoutEffect(() => {
    if (!grant?.ok || !grant.origin) return;
    const origin = grant.origin;
    const onMessage = (event: MessageEvent) => {
      const said = event.data as { reevePreview?: number; type?: string; path?: unknown; url?: unknown } | null;
      if (event.origin !== origin || !said || said.reevePreview !== 1) return;
      // Only the page in OUR frame is listened to.
      let inner: Window | null = null;
      try {
        inner = holder.current?.contentWindow?.frames[0] ?? null;
      } catch {
        inner = null;
      }
      if (inner && event.source !== inner) return;
      if (onSaid.current(said as Record<string, unknown>)) return;
      if (said.type === "page" && typeof said.path === "string" && said.path.startsWith(prefix)) {
        const rel = decode(said.path.slice(prefix.length)).slice(0, 300);
        here.current = rel.split(/[?#]/)[0];
        setPage(rel);
      }
      if (said.type === "blocked" && typeof said.url === "string") {
        let host = "";
        try {
          host = new URL(said.url).host;
        } catch {
          host = "";
        }
        if (host && !origin.endsWith(`//${host}`)) {
          setBlocked((hosts) => (hosts.includes(host) || hosts.length >= 20 ? hosts : [...hosts, host]));
        }
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [grant, prefix]);

  const tell = useCallback(
    (what: "back" | "reload") => {
      try {
        holder.current?.contentWindow?.frames[0]?.postMessage({ reevePreviewDo: what }, grant?.origin || "*");
      } catch {
        if (what === "reload") setMounts((n) => n + 1);
      }
    },
    [grant?.origin],
  );
  const post = useCallback(
    (message: Record<string, unknown>) => {
      try {
        holder.current?.contentWindow?.frames[0]?.postMessage(message, grant?.origin || "*");
      } catch {
        /* the page is gone or not ours yet: it is told again when it says where it is */
      }
    },
    [grant?.origin],
  );
  const reload = useCallback(() => tell("reload"), [tell]);
  // A new answer to "internet?" is a new key: stay on the page the person was on.
  const start =
    grant?.ok && grant.origin
      ? `${grant.origin}${prefix}${(here.current || first).split("/").map(encodeURIComponent).join("/")}`
      : "";
  const allow = () => {
    setAsking(false);
    setBlocked([]);
    setInternet(true);
  };

  return {
    grant,
    ready: !!grant?.ok,
    holder,
    mounts,
    page,
    first,
    start,
    blocked,
    internet: !!grant?.internet,
    asking,
    ask: () => setAsking(true),
    cancelAsk: () => setAsking(false),
    allow,
    turnOff: () => setInternet(false),
    tell,
    post,
    reload,
    onSaid,
  };
}

export type PageHost = ReturnType<typeof usePageHost>;

/** "Let this page use the internet?": what it tried to reach, and what saying yes lets it do. */
export function InternetAsk({ host, name }: { host: PageHost; name: string }) {
  if (!host.asking) return null;
  const { blocked } = host;
  return (
    <div className="fm-dialog-backdrop" onClick={host.cancelAsk}>
      <div
        className="fm-dialog page-ask"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="page-ask-title"
        aria-describedby="page-ask-hint"
        data-testid="page-net-dialog"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === "Escape" && host.cancelAsk()}
      >
        <div className="fm-dialog-title" id="page-ask-title">
          Let this page use the internet?
        </div>
        <div className="fm-dialog-hint" id="page-ask-hint">
          {blocked.length > 0 && (
            <>
              {name} tried to reach {blocked.slice(0, 4).join(", ")}
              {blocked.length > 4 ? ` and ${blocked.length - 4} more` : ""}.{" "}
            </>
          )}
          With the internet on, it can load code from any site and send data there, including the files in this
          conversation's folder that it can already read.
        </div>
        <div className="fm-dialog-actions">
          <button type="button" className="btn sm" autoFocus onClick={host.cancelAsk}>
            Cancel
          </button>
          <button type="button" className="btn sm primary" data-testid="page-net-confirm" onClick={host.allow}>
            Allow for this page
          </button>
        </div>
      </div>
    </div>
  );
}
