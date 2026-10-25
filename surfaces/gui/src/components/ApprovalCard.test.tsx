import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalCard, scopeNote } from "./ApprovalCard";
import { InboxItemCard } from "./InboxItemCard";
import type { ApprovalItem } from "../types";
import type { InboxItem } from "../api";

const AUTOMATION = { id: "task-1", title: "Weekly digest" };

const ask = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
  kind: "approval",
  name: "send_message",
  args: { target: "slack:T1/C1", text: "digest" },
  reason: "requires approval",
  category: "messaging",
  ...extra,
});

const card = (item: ApprovalItem, onApprove = vi.fn(), runTask?: typeof AUTOMATION) => {
  render(<ApprovalCard item={item} onApprove={onApprove} runTask={runTask} />);
  return onApprove;
};

const parked = (data?: Record<string, any>, extra: Partial<InboxItem> = {}): InboxItem => ({
  id: "i1",
  session_id: "__run__r1",
  kind: "approval",
  title: "Run `send_message`?",
  body: "target: slack:T1/C1",
  state: "pending",
  resolution: null,
  inbox: "default",
  created_at: "",
  resolved_at: null,
  data,
  ...extra,
});

const SKILL_ARGS = {
  name: "weekly-github-report",
  description: "Create a concise Monday status report from GitHub activity.",
  instructions: "1. Fetch PRs\n2. Write the report",
  files: ["fetch_prs.py", "sub/example-report.md"],
};

afterEach(cleanup);

describe("grants that outlast the call", () => {
  it("Allow every time needs both an automation run and a target it can be bound to", () => {
    const onApprove = card(ask({ standingTarget: "slack:T1/C1" }), vi.fn(), AUTOMATION);
    fireEvent.click(screen.getByText("Allow every time"));
    expect(onApprove).toHaveBeenCalledWith("always_task");
    expect(screen.queryByText("Always allow")).toBeNull(); // it takes the session grant's place
    cleanup();

    card(ask({ standingTarget: "slack:T1/C1" })); // an ordinary conversation
    expect(screen.queryByText("Allow every time")).toBeNull();
    cleanup();

    card(ask({ name: "run_shell", args: { command: "ls" }, standingTarget: undefined }), vi.fn(), AUTOMATION); // nothing to bind to
    expect(screen.queryByText("Allow every time")).toBeNull();
  });

  it("a new automation's proposed permissions: reads are told, writes are granted", () => {
    card(
      ask({
        name: "create_scheduled_task",
        args: {
          title: "Weekly digest",
          instructions: "post it",
          cron: "0 9 * * 1",
          permissions: [
            { tool: "send_message", target: "slack:T1/C1", access: "write" },
            { tool: "github_list_commits", target: "acme/agent-platform", access: "read" },
          ],
        },
      }),
    );
    const grants = screen.getByTestId("approval-grants").textContent;
    for (const text of ["slack:T1/C1", "always allowed once you approve", "acme/agent-platform", "read-only"]) expect(grants).toContain(text);
    expect(screen.queryByText(/permissions=/)).toBeNull(); // not dumped again as arguments
  });
});

describe("what the card shows of the action", () => {
  it("a file write is a card like the rest, and its content is there without a click", () => {
    const onApprove = card(ask({ name: "write_file", args: { path: "src/fetch_data.py", content: "import json\nimport urllib\nx=1\ny=2\nz=3\ndone=1" }, category: undefined }));
    const row = screen.getByTestId("approval-row").textContent;
    expect(row).toContain("Write ");
    expect(row).toContain("fetch_data.py");
    expect(screen.queryByText(/Permission required/i)).toBeNull();
    // From the arguments, since the file does not exist yet. It once sat behind a "preview"
    // toggle, and a write approved unseen is what the card is there to stop.
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.queryByText(/done=1/)).toBeNull();
    fireEvent.click(screen.getByText("Show all 6 lines"));
    expect(screen.getByText(/done=1/)).toBeTruthy();
    fireEvent.click(screen.getByText("Allow"));
    expect(onApprove).toHaveBeenCalledWith("once");
  });

  it("sending a file names the destination, shows the file and the message, and says it leaves", () => {
    card(ask({ name: "send_file", args: { target: "slack:T1/C9:1700.1", path: "out/report.pdf", comment: "here you go" } }));
    expect(screen.getByText(/Send a file to/).textContent).toContain("C9");
    expect(screen.getByText(/leaves this computer → Slack/)).toBeTruthy();
    expect(screen.getByText(/report\.pdf/)).toBeTruthy();
    expect(screen.getByText(/here you go/)).toBeTruthy();
    expect(screen.getByText("Allow once")).toBeTruthy();
  });

  it("a long message with no line breaks is cut short and can be opened", () => {
    // The owner, 2026-07-15: a one-paragraph Slack digest stretched the card to the transcript's
    // height, because the preview was cut by lines only.
    const digest = "aisuite last 24 hours of work: five PRs merged covering streaming, multimodal input, Slack improvements, human attribution, and formatting. ".repeat(8);
    card(ask({ args: { target: "slack:T1/C1", text: digest } }));
    expect(screen.getByTestId("approval-preview").textContent!.length).toBeLessThan(500);
    fireEvent.click(screen.getByText("Show the full message"));
    expect(screen.getByTestId("approval-preview").textContent!.length).toBeGreaterThan(1000);
    expect(screen.getByText("Show less")).toBeTruthy();
  });

  it("a short message is quoted in the card, with no block around it", () => {
    card(ask());
    expect(screen.getByText(/“digest”/)).toBeTruthy();
    expect(screen.queryByTestId("approval-preview")).toBeNull();
  });

  it("a command is titled by the model's description and shown, and it runs here rather than stays", () => {
    card(ask({ name: "run_shell", args: { command: "python3 fetch.py > data.json", description: "Fetch semiconductor stock data" }, category: undefined }));
    expect(screen.getByText(/Run a command — fetch semiconductor stock data/)).toBeTruthy();
    expect(screen.getByText(/python3 fetch\.py/)).toBeTruthy();
    // The process is local, but `python3 fetch.py` can reach the network like any program, and
    // "stays on this computer" would promise something no shell card can.
    expect(screen.getByText(/runs on this computer/)).toBeTruthy();
    expect(screen.queryByText(/stays on this computer/)).toBeNull();
    expect(screen.getByText("Always allow this command")).toBeTruthy();
  });

  it("there is one way to refuse, because Stop is the other", () => {
    // A "Stop this" button was here for a while. Against the composer's Stop pressed at the same
    // card it gave the same end state, step count and answers: a second way to say one thing,
    // which to someone who does not write code is a question, not a choice.
    const onApprove = card(ask({ name: "run_shell", args: { command: "git log" } }));
    fireEvent.click(screen.getByText("Deny"));
    expect(onApprove).toHaveBeenLastCalledWith("deny");
    expect(screen.queryByTestId("deny-and-stop")).toBeNull();
  });

  it("a tool's own sentence is the title, over this app's guess", () => {
    card(ask({ name: "mcp__tracker__create_issue", args: { project: "OPS" }, category: "mcp", presentation: { summary: "Create a new issue in a tracker project." } }));
    expect(screen.getByText(/Create a new issue in a tracker project\./)).toBeTruthy();
    expect(screen.queryByText(/Use mcp__tracker__create_issue/)).toBeNull();
  });

  it("a tool's own detail line is shown instead of its arguments", () => {
    card(ask({ name: "mcp__tracker__create_issue", args: { project: "OPS", title: "Disk full" }, category: "mcp", presentation: { summary: "File a ticket", detail: "OPS · “Disk full”" } }));
    expect(screen.getByText(/OPS · “Disk full”/)).toBeTruthy();
    expect(screen.queryByText(/project=OPS/)).toBeNull();
  });
});

describe("where it acts", () => {
  it("a tool nobody here can vouch for says so, and is drawn with care", () => {
    // An MCP tool's category is "mcp", which nothing above names. It used to be told as staying on
    // this computer: a promise, on the screen where it decides consent, about a server out of sight.
    card(ask({ name: "mcp__tracker__create_issue", args: { project: "OPS", title: "Disk full" }, category: "mcp" }));
    expect(screen.getByText(/can’t tell where this acts/)).toBeTruthy();
    expect(screen.queryByText(/stays on this computer/)).toBeNull();
    expect(document.querySelector(".approval-external")).toBeTruthy();
  });

  it("Reeve's own computer-use and browser servers can be named, being local by how they are built", () => {
    card(ask({ name: "mcp__computer-use__click", args: { x: 10, y: 20 }, category: "mcp" }));
    expect(screen.getByText(/controls mouse and keyboard on this computer/)).toBeTruthy();
    expect(screen.queryByText(/can’t tell where this acts/)).toBeNull();
    cleanup();
    card(ask({ name: "mcp__browser__browser_navigate", args: { url: "https://docs.python.org/3" }, category: "mcp" }));
    expect(screen.getByText(/acts in the local headless browser/)).toBeTruthy();
  });

  it("each kind of call gets its answer", () => {
    const where = (name: string, args: any = {}, more: object = {}) => scopeNote({ name, args, ...more });
    expect(where("save_skill").text).toBe("saves to Settings ▸ Skills");
    expect(where("gmail_send_email", {}, { category: "connector" })).toEqual({ text: "acts on a connected service", external: true, unknown: false });
    expect(where("web_fetch", { url: "https://www.example.com/x" }).text).toBe("leaves this computer → example.com");
    expect(where("send_message", { target: "telegram:42" }).text).toBe("leaves this computer → Telegram");
    expect(where("mcp__x__y", {}, { presented: { summary: "s", kind: "send" } }).external).toBe(true);
    expect(where("write_file", { overwrite: true }).text).toBe("stays on this computer · overwrites the existing file");
  });
});

describe("saving a skill", () => {
  const skill = () => ask({ name: "save_skill", category: "skills", args: SKILL_ARGS, standingTarget: undefined });

  it("shows the name first, then what it is, its instructions and every file it brings", () => {
    card(skill());
    expect(screen.getByText("weekly-github-report")).toBeTruthy();
    expect(screen.getAllByText(/to your skills/).length).toBeGreaterThan(0); // the title and the note
    expect(screen.getByText("saves to Settings ▸ Skills")).toBeTruthy(); // the corner says where
    expect(screen.getByText(/usable in every conversation from\s+then on/)).toBeTruthy();
    expect(screen.getByText(SKILL_ARGS.description)).toBeTruthy();
    expect(screen.getByText(/Fetch PRs/)).toBeTruthy();
    const files = screen.getByTestId("skill-bundle-files").textContent;
    expect(files).toContain("fetch_prs.py");
    expect(files).toContain("example-report.md"); // the file's name, not its path
  });

  it("is added or put off, and never allowed for the whole session", () => {
    const onApprove = card(skill());
    expect(screen.queryByText("Always allow")).toBeNull(); // every proposal is reviewed
    expect(screen.queryByText("Deny")).toBeNull();
    fireEvent.click(screen.getByText("Add to my skills"));
    expect(onApprove).toHaveBeenCalledWith("once");
    fireEvent.click(screen.getByText("Not now"));
    expect(onApprove).toHaveBeenCalledWith("deny");
  });
});

describe("requests that leave the computer", () => {
  const fetch = (extra: Partial<ApprovalItem> = {}) => ask({ name: "web_fetch", args: { url: "https://www.bbc.com/news/article-1" }, category: undefined, ...extra });

  it("a fetch can be allowed for its domain, as the server spells it, never for the whole tool", () => {
    const onApprove = card(fetch());
    fireEvent.click(screen.getByText("Always allow bbc.com this session"));
    expect(onApprove).toHaveBeenCalledWith("always_domain");
    expect(screen.queryByText("Always allow")).toBeNull();
    expect(screen.getByText(/leaves this computer → bbc\.com/)).toBeTruthy();
  });

  it("a fetch of an address that does not parse is allowed once or denied, nothing more", () => {
    card(fetch({ args: { url: "not a url" } }));
    expect(screen.queryByText(/Always allow/)).toBeNull();
    expect(screen.getByText("Allow once")).toBeTruthy();
  });

  it("a search names the engine it goes to now, and can be allowed for searches", () => {
    const onApprove = card(fetch({ name: "web_search", args: { query: "H-1B visa rule change" }, searchProvider: "duckduckgo" }));
    expect(screen.getByText(/Queries go to your configured search provider \(currently: duckduckgo\)\./)).toBeTruthy();
    fireEvent.click(screen.getByText("Always allow searches this session"));
    expect(onApprove).toHaveBeenCalledWith("always_tool"); // the tool goes to one provider
    expect(screen.getByText(/leaves this computer → your search provider/)).toBeTruthy();
  });
});

// What a command's text cannot show: the agent made the file it is about to run. A
// person approving is as blind to the script as the classifier that stopped it.
describe("where the file a command runs came from", () => {
  const command = (extra: Partial<ApprovalItem> = {}) => ask({ name: "run_shell", args: { command: "python scripts/setup.py" }, category: "shell", ...extra });

  it("says the agent wrote it, or downloaded it", () => {
    card(command({ provenance: "scripts/setup.py was created by the agent 3 steps ago" }));
    expect(screen.getByText(/created by the agent 3 steps ago/)).toBeTruthy();
    cleanup();
    card(command({ args: { command: "bash install.sh" }, provenance: "install.sh was downloaded by the agent 1 step ago" }));
    expect(screen.getByText(/downloaded by the agent/)).toBeTruthy();
  });

  it("says nothing of an ordinary command", () => {
    card(command());
    expect(screen.queryByText(/by the agent/)).toBeNull();
  });
});

describe("why it asks (D4)", () => {
  it("a boundary is out of reach: no answer here changes it", () => {
    card(ask({ reason: "path is not in a writable directory: /etc/hosts", axis: "boundary" }));
    expect(screen.getByTestId("approval-out-of-reach").textContent).toBe("Out of reach");
    expect(screen.getByText(/path is not in a writable directory/)).toBeTruthy();
  });

  it("consent needs a person's OK", () => {
    card(ask({ reason: "requires approval", axis: "consent" }));
    expect(screen.getByText("Needs your OK")).toBeTruthy();
  });

  it("an older server that says nothing gets no line", () => {
    card(ask());
    expect(screen.queryByText("Out of reach")).toBeNull();
    expect(screen.queryByText("Needs your OK")).toBeNull();
  });
});

describe("editing a call before it runs", () => {
  it("opens with the arguments as JSON and sends the edited ones to be checked again", () => {
    const onApprove = card(ask({ name: "run_shell", args: { command: "git status", cwd: "/w" } }));
    fireEvent.click(screen.getByTestId("approval-edit-open"));
    const box = screen.getByTestId("approval-edit-text") as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toEqual({ command: "git status", cwd: "/w" });
    fireEvent.change(box, { target: { value: JSON.stringify({ command: "git status --short", cwd: "/w" }, null, 2) } });
    fireEvent.click(screen.getByTestId("approval-edit-run"));
    expect(onApprove).toHaveBeenCalledWith("modify", { command: "git status --short", cwd: "/w" });
  });

  it("sends neither broken JSON nor anything that is not an object, and Cancel puts the answers back", () => {
    const onApprove = card(ask());
    fireEvent.click(screen.getByTestId("approval-edit-open"));
    const box = screen.getByTestId("approval-edit-text") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "{not json" } });
    fireEvent.click(screen.getByTestId("approval-edit-run"));
    expect(screen.getByTestId("approval-edit-error")).toBeTruthy();
    fireEvent.change(box, { target: { value: "[1,2]" } });
    fireEvent.click(screen.getByTestId("approval-edit-run"));
    expect(screen.getByTestId("approval-edit-error").textContent).toContain("JSON object");
    expect(onApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByTestId("approval-edit")).toBeNull();
    expect(screen.getByTestId("approval-edit-open")).toBeTruthy();
  });
});

describe("a read outside the session", () => {
  const outside = (extra: Partial<ApprovalItem> = {}) =>
    ask({ name: "read_file", args: { path: "D:/Project/other/notes.md" }, reason: "reads outside the session's folders: D:/Project/other", reach: [{ path: "D:/Project/other", writable: false }], category: undefined, ...extra });

  it("says what allowing lets the session do there from now on, and offers no tool-wide always", () => {
    card(outside());
    expect(screen.getByTestId("approval-reach").textContent).toBe("Allowing lets this session read D:/Project/other from now on.");
    expect(screen.queryByText("Always allow")).toBeNull();
  });

  it("a write there is read and write", () => {
    card(outside({ name: "write_file", reach: [{ path: "D:/Project/other", writable: true }] }));
    expect(screen.getByTestId("approval-reach").textContent).toBe("Allowing lets this session read and write D:/Project/other from now on.");
  });

  it("a card that reaches nothing new keeps its plain reason", () => {
    card(outside({ reach: undefined, name: "write_file", reason: "write here?" }));
    expect(screen.queryByTestId("approval-reach")).toBeNull();
    expect(screen.getByText("write here?")).toBeTruthy();
  });
});

describe("the parked card speaks as the live one does", () => {
  it("offers Allow every time only when the item carries its task and target", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={parked({ task_id: "task-1", task_title: "Weekly digest", standing_target: "slack:T1/C1" })} onResolve={onResolve} />);
    fireEvent.click(screen.getByText("Allow every time"));
    expect(onResolve).toHaveBeenCalledWith("i1", "always_task");
    cleanup();
    render(<InboxItemCard item={parked()} onResolve={vi.fn()} />); // an unattended approval with no task
    expect(screen.queryByText("Allow every time")).toBeNull();
    expect(screen.getByText("Approve")).toBeTruthy();
    expect(screen.getByText("Deny")).toBeTruthy();
  });

  it("with the call's data it gets the live card's title, preview, scope and words", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={parked({ tool: "write_file", arguments: { path: "src/fetch_data.py", content: "import json\nx = 1" } })} onResolve={onResolve} />);
    expect(screen.getByText("fetch_data.py")).toBeTruthy();
    expect(screen.queryByText("Run `send_message`?")).toBeNull();
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.getByText(/stays on this computer/)).toBeTruthy();
    fireEvent.click(screen.getByText("Allow once"));
    expect(onResolve).toHaveBeenCalledWith("i1", "allow"); // the answer's word is the same on every path
  });

  it("a parked skill has the same review and the same two answers", () => {
    const onResolve = vi.fn();
    const item = parked({ tool: "save_skill", arguments: { ...SKILL_ARGS, files: ["fetch_prs.py"] } }, { id: "i9", session_id: "s1", title: "Run `save_skill`?", body: "" });
    render(<InboxItemCard item={item} onResolve={onResolve} />);
    expect(screen.getByText("saves to Settings ▸ Skills")).toBeTruthy();
    expect(screen.getByText(SKILL_ARGS.description)).toBeTruthy();
    expect(screen.getByText(/Fetch PRs/)).toBeTruthy();
    expect(screen.getByTestId("skill-bundle-files").textContent).toContain("fetch_prs.py");
    expect(screen.getByText(/usable in every conversation/)).toBeTruthy();
    expect(screen.queryByText("Allow once")).toBeNull();
    fireEvent.click(screen.getByText("Add to my skills"));
    expect(onResolve).toHaveBeenCalledWith("i9", "allow");
    fireEvent.click(screen.getByText("Not now"));
    expect(onResolve).toHaveBeenCalledWith("i9", "deny");
  });
});
