import { PINNED_SESSION } from "../data";
import { inSession, type On } from "../router";
import type { MockState } from "../state";

// A conversation's own record (its transcript, renaming, archiving, deleting) and the skills:
// the composer's menu, Settings' list and edits, uploads and community installs,
// each staged for a preview before it is confirmed. Upload and install come before the routes
// keyed by a skill's name, whose pattern their paths would also fit.

const lastPart = (path: string) => decodeURIComponent(path.split("/").pop()!);

export function conversationRoutes(on: On, s: MockState) {
  const { sessions, skills, skillInstalls, staged } = s;

  // Every transcript reads empty unless a spec seeds one (seedSessionMessages).
  on("*", inSession("messages"), () => ({ messages: [] }));
  const at = (path: string) => sessions.findIndex((row) => row.session_id === lastPart(path));
  on("PATCH", /\/v1\/sessions\/[^/]+$/, ({ path, body }) => {
    const i = at(path);
    if (i >= 0) Object.assign(sessions[i], body);
    return { ok: true };
  });
  on("DELETE", /\/v1\/sessions\/[^/]+$/, ({ path }) => {
    const i = at(path);
    if (i >= 0) sessions.splice(i, 1);
    return { ok: true };
  });
  on("*", /\/v1\/sessions\/[^/]+$/, ({ path }) => sessions[at(path)] ?? PINNED_SESSION);

  // The composer's menu has the skills Settings has turned on, and no others.
  on("*", inSession("skills"), () => ({
    skills: skills.filter((k) => k.enabled).map((k) => ({ name: k.name, description: k.description, scope: k.scope, enabled: true })),
  }));
  const skillRow = (name: string, description: string, instructions: string, source: string, files: number) =>
    ({ name, description, instructions, scope: "global", source, enabled: true, path: `/state/skills/${name}`, files });

  // An upload is read into a preview, and nothing is added until it is confirmed. The mock reads
  // no archive: every upload is the same small skill.
  on("POST", "/v1/skills/upload/confirm", ({ body }) => {
    const upload = staged.stagedSkill;
    if (!upload || body.token !== upload.token) return { ok: false, error: "Upload expired — pick the file again." };
    skills.push(skillRow(upload.name, upload.description, upload.instructions, "uploaded", upload.files.length));
    staged.stagedSkill = null;
    return { ok: true };
  });
  on("POST", "/v1/skills/upload", () => {
    staged.stagedSkill = { token: "stage-1", name: "greet", description: "says hello", instructions: "Say hello warmly.", files: ["notes.txt"] };
    return { ok: true, ...staged.stagedSkill };
  });
  // A community install: the source is fetched into a preview (here it always finds the same
  // one), then confirmed, replacing a skill of the same name only when asked to.
  on("POST", "/v1/skills/install/confirm", ({ body }) => {
    const install = staged.stagedInstall;
    if (!install || body.token !== install.token) return { ok: false, error: "Unknown or expired install — run the install again." };
    if (!body.overwrite && install.skills.some((k: any) => k.replaces)) {
      return { ok: false, error: "A skill named 'x' is already in your skills. Confirm the replacement to install over it." };
    }
    for (const k of install.skills) {
      const row = skillRow(k.name, k.description, `Installed from ${install.source}.`, "local", k.files.length);
      const i = skills.findIndex((have) => have.name === k.name);
      if (i >= 0) skills[i] = row;
      else skills.push(row);
      skillInstalls[k.name] = { source: install.source, sourceType: install.sourceType, skillPath: k.skillPath, computedHash: k.computedHash };
    }
    staged.stagedInstall = null;
    return { ok: true, skills: install.skills.map((k: any) => ({ name: k.name })) };
  });
  on("POST", "/v1/skills/install", ({ body }) => {
    if (!body.source) return { ok: false, error: "A source is required — owner/repo, a git/URL, or a subpath in a repo." };
    staged.stagedInstall = {
      token: "install-1", source: body.source, sourceType: "github",
      skills: [{
        name: "commit-helper", description: "Writes commit messages", description_length: 23, description_capped: false,
        license: "MIT", metadata: {}, files: ["notes.txt"], size: 512,
        skillPath: ".agents/skills/commit-helper/SKILL.md", computedHash: "sha256:abc", blocked: "", replaces: false,
      }],
      warnings: [], installable: true,
    };
    return { ok: true, ...staged.stagedInstall };
  });

  const unknown = (name: string) => ({ ok: false, error: `Unknown skill: ${name}` });
  on("POST", /\/v1\/skills\/([^/]+)\/reveal$/, ({ match }) => {
    const name = decodeURIComponent(match[1]);
    return skills.some((k) => k.name === name) ? { ok: true } : unknown(name);
  });
  on("DELETE", /\/v1\/skills\/[^/]+$/, ({ path }) => {
    const i = skills.findIndex((k) => k.name === lastPart(path));
    if (i < 0) return unknown(lastPart(path));
    skills.splice(i, 1);
    return { ok: true };
  });
  on("PATCH", /\/v1\/skills\/[^/]+$/, ({ path, body }) => {
    const skill = skills.find((k) => k.name === lastPart(path));
    if (!skill) return unknown(lastPart(path));
    for (const key of ["description", "instructions"] as const) if (typeof body[key] === "string") skill[key] = body[key];
    if (typeof body.enabled === "boolean") skill.enabled = body.enabled;
    return { ok: true };
  });
  on("POST", "/v1/skills", ({ body }) => {
    if (!body.name || !(body.instructions || "").trim()) return { ok: false, error: "Skill name and instructions are required." };
    if (skills.some((k) => k.name === body.name)) return { ok: false, error: `A skill named '${body.name}' already exists in that scope.` };
    skills.push(skillRow(body.name, body.description || "", body.instructions, "local", 0));
    return { ok: true };
  });
  on("*", "/v1/skills", () => ({ skills, installs: skillInstalls }));
}
