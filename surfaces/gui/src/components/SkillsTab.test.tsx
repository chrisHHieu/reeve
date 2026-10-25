import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SkillsTab } from "./SkillsTab";

// Settings ▸ Skills against a stubbed /v1: the list and its tags, the ways a skill is added (the
// form, a shared file, a repository, a conversation), and what each one sends.

type Sent = { url: string; method: string; body: any };
type Route = [method: string, path: string, reply: unknown];

/** Answers each request from the first route whose method matches and whose path the URL contains,
 *  so a longer path goes before its prefix. Returns every request made, in order. */
function serve(...routes: Route[]): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      sent.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const hit = routes.find(([m, path]) => m === method && url.includes(path));
      return { ok: true, json: async () => (hit ? hit[2] : {}) } as Response;
    }),
  );
  return sent;
}

const listing = (...skills: object[]): Route => ["GET", "/v1/skills", { skills }];
const NONE = listing();

const REPORT = {
  name: "weekly-report",
  description: "Monday status report",
  instructions: "1. Collect updates\n2. Write it up",
  scope: "global",
  source: "local",
  enabled: true,
  path: "/skills/weekly-report",
};
const GREET = { ...REPORT, name: "greet", description: "says hello", source: "uploaded", enabled: false };

const posted = (sent: Sent[], path: string) => sent.find((s) => s.method === "POST" && s.url.includes(path));

async function addVia(door: string) {
  fireEvent.click(await screen.findByRole("button", { name: /Add skill/ }));
  fireEvent.click(screen.getByText(door));
}

function fill(fields: Record<string, string>) {
  for (const [label, value] of Object.entries(fields)) fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const open = (name: string) => fireEvent.click(screen.getByLabelText(`Open ${name}`));
const escape = () => fireEvent.keyDown(window, { key: "Escape" });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SkillsTab list", () => {
  it("shows each row with its source tag and switch, and its details on a press", async () => {
    serve(listing(REPORT, GREET));
    render(<SkillsTab />);
    expect(await screen.findByText("weekly-report")).toBeTruthy();
    expect(screen.getByText("Monday status report")).toBeTruthy();
    // Skills are global, so no row says so; where it came from is still said.
    expect(screen.queryByText("global")).toBeNull();
    expect(screen.getByText("uploaded")).toBeTruthy();
    const [on, off] = screen.getAllByRole("switch");
    expect(on.tagName).toBe("BUTTON");
    expect(on.getAttribute("aria-checked")).toBe("true");
    expect(off.getAttribute("aria-checked")).toBe("false");
    // Edit and Delete are in the details, not on the row.
    expect(screen.queryByLabelText("Delete weekly-report")).toBeNull();
    open("weekly-report");
    expect(screen.getByTestId("skill-details").getAttribute("aria-label")).toBe("weekly-report");
    expect(screen.getByTestId("skill-details-body").textContent).toContain("Collect updates");
    expect(screen.getByLabelText("Delete weekly-report").textContent).toBe("Delete");
    escape();
    expect(screen.queryByTestId("skill-details")).toBeNull();
  });

  it("says how often the agent used each skill", async () => {
    serve(listing({ ...REPORT, uses: 12, last_used_at: Date.UTC(2026, 8, 22, 12) / 1000 }, { ...GREET, uses: 0, last_used_at: null }));
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    expect(screen.getByTestId("skill-meta-weekly-report").textContent).toMatch(/^used 12× · last \w+ 22$/);
    expect(screen.getByTestId("skill-meta-greet").textContent).toBe("not used yet");
  });

  it("tags only the skill with no description as hidden from the model", async () => {
    serve(listing({ ...REPORT, name: "quiet", description: "", path: "/skills/quiet" }, REPORT));
    render(<SkillsTab />);
    await screen.findByText("quiet");
    const tag = await screen.findByText("hidden from the model — no description");
    expect(tag.getAttribute("title")).toContain("menu skips skills without a description");
    expect(screen.getAllByText("hidden from the model — no description")).toHaveLength(1);
  });

  it("counts files only for a skill that brings some, and offers its folder", async () => {
    serve(
      listing(
        { name: "plain", description: "d", instructions: "i", scope: "global", source: "local", enabled: true, path: "/p", files: 0 },
        { name: "rich", description: "d", instructions: "i", scope: "global", source: "uploaded", enabled: true, path: "/r", files: 3 },
      ),
    );
    render(<SkillsTab />);
    expect((await screen.findByText("3 files")).textContent).toContain("3 files");
    expect(screen.getAllByText(/\d+ files?$/)).toHaveLength(1);
    open("rich");
    expect(screen.getByText("Show folder")).toBeTruthy();
    escape();
    open("plain");
    expect(screen.queryByText("Show folder")).toBeNull();
  });

  it("leaves a skill from a shared folder without a switch, Edit or Delete", async () => {
    serve(listing({ ...REPORT, name: "shared-skill", scope: "compat", compat: true, path: "/home/.claude/skills/shared-skill" }, REPORT));
    render(<SkillsTab />);
    await screen.findByText("shared-skill");
    expect(screen.getByText("shared folder")).toBeTruthy();
    expect(screen.getAllByRole("switch")).toHaveLength(1);
    open("shared-skill");
    expect(screen.queryByTestId("skill-details-edit")).toBeNull();
    expect(screen.queryByLabelText("Delete shared-skill")).toBeNull();
    escape();
    open("weekly-report");
    expect(screen.getByTestId("skill-details-edit")).toBeTruthy();
    expect(screen.getByLabelText("Delete weekly-report")).toBeTruthy();
  });

  it("shows the license, author and version the file declares", async () => {
    serve(listing({ ...REPORT, license: "MIT", metadata: { author: "Ada", version: "2" } }));
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    expect(screen.getByText(/License MIT · by Ada · v2$/)).toBeTruthy();
  });

  it("tags a skill only the person runs and one only the agent runs", async () => {
    serve(listing({ ...REPORT, name: "run-me", model_invocable: false, user_invocable: true }, { ...REPORT, name: "auto-only", model_invocable: true, user_invocable: false }));
    render(<SkillsTab />);
    expect((await screen.findByText("user-only")).getAttribute("title")).toContain("Run this one yourself");
    expect(screen.getByText("model-only").getAttribute("title")).toContain("can't be run from a conversation");
  });
});

describe("SkillsTab changes", () => {
  it("turning a skill off sends {enabled: false} and says what that means", async () => {
    const sent = serve(listing(REPORT, GREET), ["PATCH", "/v1/skills/weekly-report", { ok: true }]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    fireEvent.click(screen.getByLabelText("weekly-report enabled"));
    await waitFor(() => expect(sent.find((s) => s.method === "PATCH")?.body).toMatchObject({ enabled: false }));
    const status = (await screen.findByRole("status")).textContent;
    expect(status).toContain("weekly-report");
    expect(status).toContain("turned off everywhere");
    expect(status).toContain("clean slate");
  });

  it("deletes only on the second press", async () => {
    const sent = serve(listing(REPORT, GREET), ["DELETE", "/v1/skills/weekly-report", { ok: true }]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    open("weekly-report");
    fireEvent.click(screen.getByLabelText("Delete weekly-report"));
    expect(sent.some((s) => s.method === "DELETE")).toBe(false);
    fireEvent.click(await screen.findByText("Confirm delete"));
    await waitFor(() => expect(sent.some((s) => s.method === "DELETE" && s.url.includes("weekly-report"))).toBe(true));
  });

  it("Edit opens the form with the name fixed and the body loaded, and saves with PATCH", async () => {
    const sent = serve(listing(REPORT, GREET), ["PATCH", "/v1/skills/weekly-report", { ok: true }]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    open("weekly-report");
    fireEvent.click(screen.getByTestId("skill-details-edit"));
    expect(screen.queryByTestId("skill-details")).toBeNull();
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("weekly-report");
    expect(name.disabled).toBe(true);
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toContain("Collect updates");
    fill({ Instructions: "New steps" });
    fireEvent.click(screen.getByText("Save skill"));
    await waitFor(() => {
      const patch = sent.find((s) => s.method === "PATCH");
      expect(patch?.url).toContain("/v1/skills/weekly-report");
      expect(patch?.body.instructions).toBe("New steps");
    });
  });
});

describe("SkillsTab adding", () => {
  it("the menu has three ways in, and the conversation one only hands off", async () => {
    const sent = serve(NONE);
    const onCreateSkill = vi.fn();
    render(<SkillsTab onCreateSkill={onCreateSkill} />);
    fireEvent.click(await screen.findByRole("button", { name: /Add skill/ }));
    expect(screen.getByText("Write it myself")).toBeTruthy();
    expect(screen.getByText("Import a file")).toBeTruthy();
    expect(screen.getByText(/you review before it installs/)).toBeTruthy();
    expect(screen.getByText(/asks before adding it to\s+your skills/)).toBeTruthy();
    fireEvent.click(screen.getByText("Create with Reeve"));
    expect(onCreateSkill).toHaveBeenCalledWith("");
    expect(sent.some((s) => s.method === "POST")).toBe(false);
  });

  it("adding is the menu alone, and the menu closes on a pick", async () => {
    serve(NONE);
    render(<SkillsTab onCreateSkill={vi.fn()} />);
    await screen.findByRole("button", { name: /Add skill/ });
    for (const gone of ["Start a conversation", "Ask Reeve to revise"]) expect(screen.queryByText(gone)).toBeNull();
    expect(screen.queryByLabelText("Describe the skill")).toBeNull();
    expect(screen.queryByText(/Not a chat/)).toBeNull();
    await addVia("Write it myself");
    expect(screen.queryByText("Write it myself")).toBeNull();
    expect(screen.getByText("Save skill")).toBeTruthy();
  });

  it("the form asks nothing about where a skill applies", async () => {
    serve(NONE);
    render(<SkillsTab />);
    await addVia("Write it myself");
    expect(screen.queryByText("Available in")).toBeNull();
    expect(screen.queryByLabelText("Everywhere")).toBeNull();
    expect(screen.queryByLabelText("Only one project")).toBeNull();
    expect(screen.queryByText(/Move to/)).toBeNull();
  });

  it("Save waits for a name and instructions", async () => {
    serve(NONE);
    render(<SkillsTab />);
    await addVia("Write it myself");
    const save = screen.getByText("Save skill") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fill({ Name: "greet" });
    expect(save.disabled).toBe(true);
    fill({ Instructions: "Say hello." });
    expect(save.disabled).toBe(false);
  });

  it("a blank description is refused here, before anything is sent", async () => {
    const sent = serve(NONE, ["POST", "/v1/skills", { ok: true }]);
    render(<SkillsTab />);
    await addVia("Write it myself");
    fill({ Name: "greet", Instructions: "Say hello." });
    fireEvent.click(screen.getByText("Save skill"));
    expect((await screen.findByRole("alert")).textContent).toContain("description");
    expect(sent.some((s) => s.method === "POST")).toBe(false);
    expect(screen.getByText("Save skill")).toBeTruthy();
  });

  it("a new skill is posted without a workspace, the list reloads and the line names it", async () => {
    const sent = serve(NONE, ["POST", "/v1/skills", { ok: true }]);
    render(<SkillsTab />);
    await addVia("Write it myself");
    fill({ Name: "greet", Description: "says hello", Instructions: "Say hello." });
    fireEvent.click(screen.getByText("Save skill"));
    await waitFor(() => {
      const post = sent.find((s) => s.method === "POST" && s.url.endsWith("/v1/skills"));
      expect(post?.body).toMatchObject({ name: "greet", description: "says hello", instructions: "Say hello." });
      expect(post?.body.workspace).toBeUndefined();
    });
    expect(sent.filter((s) => s.method === "GET" && s.url.includes("/v1/skills")).length).toBeGreaterThan(1);
    const status = (await screen.findByRole("status")).textContent;
    expect(status).toContain("greet");
    expect(status).toContain("can now use it in every conversation");
  });

  it("an error from the server is shown in the form", async () => {
    serve(NONE, ["POST", "/v1/skills", { ok: false, error: "A skill named 'x' already exists in that scope." }]);
    render(<SkillsTab />);
    await addVia("Write it myself");
    fill({ Name: "x", Description: "d", Instructions: "y" });
    fireEvent.click(screen.getByText("Save skill"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/already exists/)).toBeTruthy();
  });

  it("a shared file is read back for review and installed only on confirm", async () => {
    const sent = serve(
      ["POST", "/v1/skills/upload/confirm", { ok: true }],
      ["POST", "/v1/skills/upload", { ok: true, token: "t1", name: "greet", description: "says hello", instructions: "Say hello warmly.", files: ["notes.txt"] }],
      NONE,
    );
    render(<SkillsTab />);
    const input = (await screen.findByLabelText("Upload a skill archive")) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([80, 75, 3, 4])], "greet.zip", { type: "application/zip" })] } });
    await screen.findByText("Review before installing");
    expect(screen.getByText("Say hello warmly.")).toBeTruthy();
    expect(screen.getByText(/notes\.txt/)).toBeTruthy();
    expect(posted(sent, "/upload/confirm")).toBeUndefined();
    fireEvent.click(screen.getByText("Install skill"));
    await waitFor(() => expect(posted(sent, "/upload/confirm")?.body).toMatchObject({ token: "t1" }));
  });
});

describe("SkillsTab install from a repository", () => {
  const helper = {
    name: "commit-helper",
    description: "Writes commit messages",
    description_length: 23,
    description_capped: false,
    license: "MIT",
    metadata: {},
    files: ["notes.txt"],
    size: 512,
    skillPath: ".agents/skills/commit-helper/SKILL.md",
    computedHash: "sha256:abc",
    blocked: "",
    replaces: false,
  };
  const wordy = {
    ...helper,
    name: "verbose",
    description: "A very long one",
    description_length: 620,
    description_capped: true,
    license: "",
    files: [],
    size: 120,
    skillPath: ".agents/skills/verbose/SKILL.md",
    computedHash: "sha256:def",
  };
  const staged = (over: object = {}) => ({
    ok: true,
    token: "install-1",
    source: "owner/repo",
    sourceType: "github",
    agent: "universal",
    command: ["npx", "-y", "skills@latest", "add", "owner/repo", "--copy", "-y", "-a", "universal"],
    installable: true,
    warnings: [],
    skills: [helper, wordy],
    ...over,
  });

  async function run(source = "owner/repo") {
    await addVia("Install from repo…");
    fill({ Source: source });
    fireEvent.click(screen.getByText("Run"));
  }

  it("says it uses npx and sends the source and the names", async () => {
    const sent = serve(["POST", "/v1/skills/install", staged()], NONE);
    render(<SkillsTab />);
    await addVia("Install from repo…");
    expect(screen.getByText(/uses npx skills/)).toBeTruthy();
    fill({ Source: "owner/repo", "Skills (optional)": "one, two" });
    fireEvent.click(screen.getByText("Run"));
    await waitFor(() => expect(posted(sent, "/v1/skills/install")?.body).toMatchObject({ source: "owner/repo", skills: ["one", "two"] }));
  });

  it("reviews each fetched skill: its description against the cap, its files, the warnings", async () => {
    serve(["POST", "/v1/skills/install", staged({ warnings: ["Skipped broken.md: missing 'name'"] })], NONE);
    render(<SkillsTab />);
    await run();
    expect(await screen.findByText("Review before installing")).toBeTruthy();
    expect(screen.getByText("commit-helper")).toBeTruthy();
    expect(screen.getByText("23/500")).toBeTruthy();
    expect(screen.getByText(/1 bundled file \(notes\.txt\)/)).toBeTruthy();
    expect(screen.getByText("620/500").getAttribute("title")).toContain("500-character");
    expect(screen.getByText(/catalog cap/)).toBeTruthy();
    expect(screen.getByText(/Skipped broken\.md/)).toBeTruthy();
  });

  it("installs on confirm, and the row then names where it came from", async () => {
    const sent = serve(
      ["POST", "/v1/skills/install/confirm", { ok: true }],
      ["POST", "/v1/skills/install", staged()],
      [
        "GET",
        "/v1/skills",
        {
          skills: [{ ...REPORT, name: "commit-helper", source: "local" }],
          installs: { "commit-helper": { source: "owner/repo", sourceType: "github", skillPath: ".agents/skills/commit-helper/SKILL.md", computedHash: "sha256:abc" } },
        },
      ],
    );
    render(<SkillsTab />);
    await run();
    await screen.findByText("Review before installing");
    expect(posted(sent, "/install/confirm")).toBeUndefined();
    fireEvent.click(screen.getByText("Install skills"));
    await waitFor(() => expect(posted(sent, "/install/confirm")?.body).toMatchObject({ token: "install-1", overwrite: false }));
    expect((await screen.findByText("owner/repo")).getAttribute("title")).toContain("Installed from owner/repo");
    open("commit-helper");
    expect(screen.getByLabelText("Delete commit-helper").getAttribute("title")).toBe("Remove (uninstall)");
  });

  it("a skill that would hide a shared-folder one cannot be confirmed", async () => {
    const sent = serve(
      ["POST", "/v1/skills/install/confirm", { ok: false, error: "nope" }],
      ["POST", "/v1/skills/install", staged({ installable: false, skills: [{ ...helper, blocked: "'shared' already comes from a shared skills folder — move it first." }] })],
      NONE,
    );
    render(<SkillsTab />);
    await run();
    expect(await screen.findByText("can't install")).toBeTruthy();
    expect(screen.getByText(/shared skills folder/)).toBeTruthy();
    const confirm = screen.getByText("Install skills") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(posted(sent, "/install/confirm")).toBeUndefined();
  });

  it("replacing the person's own skill of the same name is said and sent as overwrite", async () => {
    const sent = serve(["POST", "/v1/skills/install/confirm", { ok: true }], ["POST", "/v1/skills/install", staged({ skills: [{ ...helper, replaces: true }] })], NONE);
    render(<SkillsTab />);
    await run();
    expect(await screen.findByText("replaces your copy")).toBeTruthy();
    fireEvent.click(screen.getByText("Replace skills"));
    await waitFor(() => expect(posted(sent, "/install/confirm")?.body).toMatchObject({ token: "install-1", overwrite: true }));
  });

  it("an installer failure is shown and nothing is staged", async () => {
    serve(["POST", "/v1/skills/install", { ok: false, error: "The skills installer failed (exit 1): repository not found" }], NONE);
    render(<SkillsTab />);
    await run("owner/missing");
    expect((await screen.findByRole("alert")).textContent).toContain("repository not found");
    expect(screen.queryByText("Review before installing")).toBeNull();
  });
});
