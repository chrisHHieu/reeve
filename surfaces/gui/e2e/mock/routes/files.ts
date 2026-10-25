import { makeBook } from "../../../src/components/rail/sheet/book.fixture";
import { baseName } from "../data";
import { inSession, type On } from "../router";
import type { MockState } from "../state";

// The session's folders and files: its roots, what a path reads as, the Files tab's edits and
// the composer's lists; the preview door's grants; and the design tools.

const ROOT = "/Users/test/Reeve/launch-note";
const MTIME = 1700000000000;
/** An 8px grey square, as Reeve's browser shoots a marked area. */
const SQUARE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGNgGAWjYBSMAggAAAQQAAGFP6pyAAAAAElFTkSuQmCC";
const svg = (markup: string) => "data:image/svg+xml;base64," + Buffer.from(markup).toString("base64");
const folder = (path: string, entries: [string, boolean, number][]) =>
  ({ ok: true, path, kind: "folder", entries: entries.map(([name, dir, size]) => ({ name, dir, size })) });
const inFolder = (rel: string) => ({ path: rel, abs_path: `${ROOT}/${rel}`, name: rel.split("/").pop()! });

export function fileRoutes(on: On, s: MockState, DOOR: string) {
  const { roots } = s;

  // The folders the session reaches. Adding one already there sets its access (the
  // read-only switch); the first one, the session's own, cannot be removed.
  on("POST", inSession("roots"), ({ body }) => {
    const known = roots.find((r) => r.path === body.path);
    if (known) known.writable = !!body.writable;
    else roots.push({ path: body.path, writable: !!body.writable, label: baseName(body.path), primary: false, exists: true });
    return { ok: true, roots };
  });
  on("DELETE", inSession("roots"), ({ query }) => {
    const at = roots.findIndex((r) => r.path === query.get("path") && !r.primary);
    if (at >= 0) roots.splice(at, 1);
    return { ok: true, roots };
  });
  on("*", inSession("roots"), () => ({ roots }));

  // What a path reads as. A root is a folder; so are design/ and reports/ (a design and its two
  // variants in each). Anything not known reads as the security review, a page that tests its
  // own sandbox: a script that says it ran, a reach for the parent window, an image off the web.
  on("*", inSession("artifacts/read"), async ({ query }) => {
    const path = query.get("path") || "";
    if (roots.some((r) => r.path === path)) return folder(path, [["reports", true, 0], ["notes.md", false, 128]]);
    if (path === "design" || path.endsWith("/design")) {
      return folder(path, [["pricing.html", false, 300], ["pricing.a.html", false, 300], ["pricing.b.html", false, 300]]);
    }
    if (path.endsWith("design/pricing.html")) return { ok: true, path, kind: "html", content: "<h1>Plans</h1>\n<p class=\"note\">Start free.</p>", mtime_ms: MTIME };
    if (path.endsWith("/reports")) {
      return folder(path, [["security-review.html", false, 2048], ["plan.html", false, 300], ["plan.a.html", false, 300], ["plan.b.html", false, 300]]);
    }
    // A real workbook, built a part at a time by the app's own test fixture, handed over as the
    // server does: its bytes in a data URL.
    if (path.endsWith("budget.xlsx")) {
      return { ok: true, path, kind: "sheet", data_url: "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64," + (await makeBook()) };
    }
    if (path.endsWith("totals.py")) return { ok: true, path, kind: "code", content: "def total(rows):\n    return sum(rows)\n", size: 38, mtime_ms: MTIME };
    if (path.endsWith("notes.md")) return { ok: true, path, kind: "markdown", content: "# Notes\n\nhello from the explorer", size: 32, mtime_ms: MTIME };
    const probe = `document.getElementById("probe")`;
    return {
      ok: true, path: "reports/security-review.html", kind: "html",
      content: [
        "<h1>Security review</h1>",
        '<div id="probe">script did not run</div>',
        "<script>",
        `  ${probe}.textContent = "script ran in sandbox";`,
        "  try { window.parent.document.title = 'ESCAPED'; } catch (e) {",
        `    ${probe}.textContent += " · parent blocked";`,
        "  }",
        "</script>",
        `<img src="https://evil.example/exfil.png" onerror="document.getElementById('probe').textContent += ' · network blocked'">`,
      ].join("\n"),
    };
  });

  // A page's way through the preview door: a key to the door, open to the internet or not.
  on("POST", inSession("preview/grant"), ({ body }) => {
    const rel = String(body.path || "").replace(/^.*\/((?:reports|design)\/)/, "$1");
    const internet = body.internet === true;
    const key = internet ? "open" : "shut";
    return { ok: true, origin: DOOR, key, internet, url: `${DOOR}/s/${key}/${rel}` };
  });
  on("*", "/v1/preview/revoke", () => ({ ok: true }));
  on("POST", inSession("design/shot"), () => ({ ok: true, png: SQUARE }));

  // Locking a design, answered with the keys of `design_lock.py` and
  // `handoff.lock_summary`; a lock is kept by its page's path.
  const designLocks = new Map<string, { locked: boolean; locked_at: number; changed: boolean; handoff: string; accepted: number }>();
  const target = { name: "northwind-web", path: "C:/work/northwind-web" };
  on("GET", inSession("design/lock"), ({ query }) => ({ ok: true, state: designLocks.get(query.get("path") || "") ?? { locked: false }, target }));
  on("*", inSession("design/lock"), ({ body }) => {
    const path = String(body.path || "");
    const handoff = path.replace(/\.html?$/, ".handoff.md");
    if (body.preview !== true) {
      const state = { locked: true, locked_at: Date.now(), changed: false, handoff, accepted: 1 };
      designLocks.set(path, state);
      return { ok: true, state, handoff, lock_file: "design/.locks/pricing.html.json", target };
    }
    // What locking would hand over: the design's measures, and the app it is built into.
    return {
      ok: true, state: designLocks.get(path) ?? { locked: false }, target,
      summary: {
        page: path, widths: [1440, 834, 390],
        colors: ["#fbf7f0", "#1b1b1b", "#1f5c4a", "#e6dfd3", "#f3ead9", "#5f5a52"], colors_count: 6, colors_unnamed: ["#f3ead9", "#5f5a52"],
        design_md: "DESIGN.md", fonts: ["Newsreader", "Be Vietnam Pro"], sizes: [56, 40, 28, 17, 15], spacing: [16, 24, 8, 40, 4, 64],
        parts: ["header", "Plans", "Compare", "Questions", "footer"], parts_count: 5, states: 4, opens: 1,
        motion: ["opacity 240 ms", "transform 200 ms"],
        accepted: ["Text under 4.5:1: section.faq > p is #8a8580 on #fbf7f0, 3.9:1 (4 places)."],
        app: {
          found: true, root: ".", stack: "React + Vite + Tailwind", router: "react-router", theme: ["tailwind.config.ts", "src/index.css"],
          colors: 9, fonts: ["Newsreader"], components: 20, component_dir: "src/components",
          scripts: { dev: "npm run dev", test: "npm run test" }, colors_new: 2, fonts_new: ["Be Vietnam Pro"],
        },
      },
    };
  });
  // The app beside its locked design, with the keys of `design_review.py`.
  on("POST", inSession("design/review"), ({ body }) => {
    const state = designLocks.get(String(body.path)) ?? { locked: true, locked_at: Date.now() - 60000, changed: false, handoff: "design/pricing.handoff.md", accepted: 0 };
    const compared = body.compare === false ? {} : {
      findings: [
        { id: "columns:plans@1440", kind: "fix", text: "At 1440 px “Plans” is 1 across in the app and 3 in the design" },
        { id: "colors-unused:ab12", kind: "fix", text: "Design colors the app does not use: #1f5c4a (background, `button` in the design)" },
        { id: "colors-added:cd34", kind: "check", text: "Colors in the app the design does not have: #2563eb (background, `button`)" },
      ],
      widths: [1440, 834, 390], issues: 2, locked: true, changed_since_lock: false,
    };
    return { ok: true, page: body.path, url: body.url, width: body.width, design_png: SQUARE, app_png: SQUARE, state: { ...state, kept: 0, reviewed: null }, ...compared };
  });
  on("POST", inSession("design/review/(keep|done)"), ({ match }) => {
    const done = match[1] === "done";
    return {
      ok: true,
      state: { locked: true, locked_at: Date.now() - 60000, changed: false, kept: done ? 0 : 1, reviewed: done ? { url: "http://localhost:5173/pricing", at: Date.now(), issues: 1 } : null },
    };
  });

  // A design taken out: saved to Downloads, or a picture or its HTML for the clipboard.
  // One editor on this machine, and the Browser tab's page read by Reeve.
  const downloaded = (name: string) => ({ ok: true, saved: `C:/Users/someone/Downloads/${name}`, name, folder: "Downloads" });
  on("POST", inSession("design/export"), ({ body }) => {
    if (body.deliver === "data") return body.kind === "png" ? { ok: true, png: SQUARE } : { ok: true, html: "<!doctype html><title>Plans</title>" };
    const stem = String(body.path || "design").split("/").pop()!.replace(/\.html?$/, "");
    const suffix = ({ pdf: ".pdf", png: ".png", zip: ".zip", html: ".standalone.html" } as Record<string, string>)[String(body.kind)] || "";
    return downloaded(`${stem}${suffix}`);
  });
  on("*", "/v1/downloads/reveal", () => ({ ok: true }));
  on("*", "/v1/editors", () => ({ ok: true, editors: [{ id: "vscode", name: "VS Code" }] }));
  on("*", inSession("open-in"), () => ({ ok: true }));
  on("POST", "/v1/browser/capture", ({ body }) => {
    if (body.kind === "brief") return { ok: true, title: "Example", brief: "The look of example.com, as Reeve's browser read it at 1280 px wide:\n- Colours: text #111111; grounds #ffffff." };
    if (body.kind === "html") return downloaded("example.com.html");
    return { ok: true, png: SQUARE };
  });
  // The conversation's designs: a page with two variants, a locked one, and the project's DESIGN.md.
  on("*", inSession("design/files"), () => {
    const now = Date.now();
    const thumb = svg('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#fff"/><rect x="20" y="20" width="120" height="18" fill="#6a3577"/></svg>');
    const clean = "Checked at 1440, 834 and 390 px: nothing to fix";
    const variant = (letter: string, age: number) => ({
      path: `design/pricing.${letter}.html`, name: `pricing.${letter}.html`, mtime_ms: now - age, checks: 1, summary: "Checked: nothing to fix", issues: 0,
      variant_of: "design/pricing.html", letter,
    });
    return {
      ok: true,
      files: [
        { path: "design/pricing.html", name: "pricing.html", mtime_ms: now - 60000, checks: 2, summary: clean, issues: 0, thumb, checked_at: now / 1000 - 60 },
        variant("a", 50000),
        variant("b", 40000),
        {
          path: "design/review.html", name: "review.html", mtime_ms: now - 90000, checks: 2, summary: clean, issues: 0,
          lock: { locked: true, locked_at: now - 80000, changed: false, handoff: "design/review.handoff.md", accepted: 0 },
        },
      ],
      design_md: { path: "DESIGN.md", colors: ["#6a3577", "#1e1a22", "#efe4f2"] },
    };
  });
  // Two checks of the review page, its versions.
  on("*", inSession("design/versions"), () => {
    const thumb = (width: string) =>
      svg(`<svg xmlns="http://www.w3.org/2000/svg" width="240" height="320"><rect width="240" height="320" fill="#fff"/><rect x="16" y="20" width="${width}" height="22" fill="#1e1a24"/></svg>`);
    const at = Date.now() / 1000;
    return {
      ok: true,
      versions: [
        { n: 1, call_id: "w1", ts: at - 600, summary: "Checked at 1440, 834 and 390 px: 2 to fix", issues: 2, changes: [], unasked: [], thumb: thumb("180") },
        {
          n: 2, call_id: "w2", ts: at - 60, summary: "Checked at 1440, 834 and 390 px: nothing to fix", issues: 0,
          changes: ["h1 type 28px/700 → 22px/700", "main > a “Privacy” removed"], unasked: ["main > a “Privacy” removed"], thumb: thumb("120"),
        },
      ],
    };
  });

  // The Files tab: a save (and a retype in a design's preview) is written as asked; new, rename,
  // move and delete answer with the path that results, relative, as the server's do.
  on("POST", inSession("files/write"), ({ body }) => ({ ok: true, path: body.path, size: String(body.content || "").length, mtime_ms: 1700000000001 }));
  on("*", inSession("artifacts/reveal"), () => ({ ok: true }));
  on("POST", inSession("files/(create|rename|move|delete)"), ({ match, body }) => {
    const path = String(body.path || "");
    const verb = match[1];
    if (verb === "create" || verb === "delete") return { ok: true, path };
    if (verb === "rename") {
      const dir = path.split("/").slice(0, -1).join("/");
      return { ok: true, path: dir ? `${dir}/${body.name}` : String(body.name || "") };
    }
    const name = path.split("/").pop();
    return { ok: true, path: body.to ? `${body.to}/${name}` : name };
  });
  // The composer's "@": every file of the session, read once and filtered there.
  on("GET", inSession("files/index"), () => ({ ok: true, files: ["notes.md", "reports/security-review.html", "reports/q3/summary.md", "totals.py"], truncated: false }));
  // `/open`'s picker: a few files, filtered by `q` as the server's walk is. Their absolute paths
  // are ones the reader takes, so a pick opens in the side panel by the ordinary read. The code
  // file and the workbook turn up only when searched for, so the full list other specs count is
  // the two files it always was.
  on("GET", inSession("files"), ({ query }) => {
    const q = (query.get("q") || "").toLowerCase();
    const hit = (f: { path: string }) => f.path.toLowerCase().includes(q);
    const listed = ["reports/security-review.html", "notes.md"].map(inFolder);
    const searched = ["totals.py", "budget.xlsx"].map(inFolder);
    return { ok: true, files: [...listed.filter((f) => !q || hit(f)), ...searched.filter((f) => q && hit(f))], truncated: false };
  });
  on("*", inSession("artifacts"), () => ({
    artifacts: [{ ...inFolder("reports/security-review.html"), kind: "html", size: 2048, modified_at: Math.floor(Date.now() / 1000) - 60 }],
  }));
}
