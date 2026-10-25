"""The page shown in the person's browser when a loopback sign-in finishes.

One small card: the Reeve mark, a result icon (a success carries the connector's initial), a
title, a friendly line and, on a failure, the raw error kept as the debugging breadcrumb. It must
render offline, so the CSS is inline, light and dark follow `prefers-color-scheme`, and no asset
is fetched.
"""

from __future__ import annotations

import html

_ACCENT = "#3670b2"
# The GUI owns the real logos. A page that renders offline with no assets uses a coloured
# initial instead.
_BADGE_COLORS = {
    "slack": "#4A154B",
    "github": "#24292f",
    "gmail": "#ea4335",
    "google_calendar": "#4285f4",
}

_STYLE = (
    ":root{--paper:#f6f5f2;--panel:#fff;--line:#e4e2dc;--ink:#2c2c2a;--muted:#6f6e68;"
    "--faint:#a3a19a;--accent:#3670b2;--ok:#2e7d4f;--ok-soft:#e3f2e9;--bad:#b3423a;"
    "--bad-soft:#f8e7e5}"
    "@media(prefers-color-scheme:dark){:root{--paper:#191918;--panel:#232322;"
    "--line:#373633;--ink:#e8e6e1;--muted:#9d9b94;--faint:#6b6a64;--accent:#6ba3dd;"
    "--ok:#5cb884;--ok-soft:#20362a;--bad:#d97b74;--bad-soft:#3a2422}}"
    "body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;"
    "justify-content:center;gap:18px;background:var(--paper);color:var(--ink);"
    'font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px}'
    ".card{background:var(--panel);border:1px solid var(--line);border-radius:16px;"
    "padding:34px 32px 28px;max-width:320px;width:100%;text-align:center;"
    "box-shadow:0 10px 30px rgba(0,0,0,.06);box-sizing:border-box}"
    ".mark{display:flex;align-items:center;justify-content:center;gap:7px;margin-bottom:22px;"
    "font-size:13px;font-weight:650}"
    ".mark i{width:20px;height:20px;border-radius:6px;background:var(--accent);"
    "display:inline-block;position:relative}"
    ".mark i::after{content:'';position:absolute;inset:5px;border-radius:2px;"
    "background:conic-gradient(from 0deg,#fff 0 25%,transparent 0 50%,#fff 0 75%,transparent 0)}"
    ".ico{width:52px;height:52px;border-radius:50%;margin:0 auto 14px;display:flex;"
    "align-items:center;justify-content:center;font-size:24px;position:relative}"
    ".ico.ok{background:var(--ok-soft);color:var(--ok)}"
    ".ico.bad{background:var(--bad-soft);color:var(--bad)}"
    ".mini{position:absolute;right:-3px;bottom:-3px;width:22px;height:22px;border-radius:7px;"
    "display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;"
    "font-weight:700;border:2px solid var(--panel)}"
    "h1{font-size:17px;font-weight:650;margin:0 0 6px;letter-spacing:-.01em}"
    "p{font-size:12.5px;color:var(--muted);margin:0}"
    ".err{font-size:11.5px;color:var(--bad);background:var(--bad-soft);border-radius:8px;"
    "padding:7px 10px;margin-top:12px;text-align:left;word-break:break-word}"
    ".foot{font-size:10.5px;color:var(--faint)}"
)


def _icon(ok: bool, connector: str) -> str:
    if not ok:
        return '<div class="ico bad">✕</div>'
    badge = ""
    if connector:
        color = _BADGE_COLORS.get(connector, _ACCENT)
        initial = html.escape((connector[:1] or "?").upper())
        badge = f'<span class="mini" style="background:{color}">{initial}</span>'
    return f'<div class="ico ok">✓{badge}</div>'


def browser_page(title: str, detail: str, *, ok: bool = True, error: str = "", connector: str = "") -> str:
    breadcrumb = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} — Reeve</title><style>{_STYLE}</style></head><body>"
        '<div class="card"><div class="mark"><i></i>Reeve</div>'
        f"{_icon(ok, connector)}<h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>{breadcrumb}</div>"
        '<div class="foot">Served locally by Reeve on this computer</div>'
        "</body></html>"
    )
