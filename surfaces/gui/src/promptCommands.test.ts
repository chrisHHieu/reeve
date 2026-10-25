// The server-command substitution
// mirrored into `promptCommands.ts`: positional `$n` (the highest takes the tail),
// `$ARGUMENTS`, the append-when-unused rule, and the quote-aware argument split that feeds
// them. The two shipped templates are checked for the placeholders the substitution needs.
import { describe, expect, it } from "vitest";
import {
  PROMPT_COMMANDS,
  parsePromptArgs,
  promptCommandFor,
  promptCommandInput,
  resolvePromptTemplate,
} from "./promptCommands";

describe("parsePromptArgs", () => {
  it("splits on whitespace and strips surrounding quotes", () => {
    expect(parsePromptArgs(`"feat/login" 'fix the flaky test' plain`)).toEqual([
      "feat/login",
      "fix the flaky test",
      "plain",
    ]);
  });

  it("keeps an [Image N] token whole and returns [] for an empty tail", () => {
    expect(parsePromptArgs("[Image 1] explain")).toEqual(["[Image 1]", "explain"]);
    expect(parsePromptArgs("   ")).toEqual([]);
  });
});

describe("resolvePromptTemplate", () => {
  it("substitutes $ARGUMENTS with the raw argument string", () => {
    expect(resolvePromptTemplate("Focus:\n$ARGUMENTS\n", "the gui layer")).toBe(
      "Focus:\nthe gui layer",
    );
  });

  it("substitutes $1.. and gives the highest placeholder the remaining tail", () => {
    expect(resolvePromptTemplate("$1 ~ $2", "src one two")).toBe("src ~ one two");
    // The HIGHEST placeholder present consumes from its own position to the end (the reference design's
    // `args.slice(argIndex).join(" ")`), so `$1 ~ $3` drops the middle argument.
    expect(resolvePromptTemplate("$1 ~ $3", "one two three")).toBe("one ~ three");
  });

  it("expands an out-of-range position to nothing", () => {
    expect(resolvePromptTemplate("[$1][$2]", "only")).toBe("[only][]");
  });

  it("appends a non-empty tail when the template uses neither placeholder form", () => {
    expect(resolvePromptTemplate("Do the thing.", "and then some")).toBe(
      "Do the thing.\n\nand then some",
    );
    expect(resolvePromptTemplate("Do the thing.", "")).toBe("Do the thing.");
  });

  it("trims the resolved prompt", () => {
    expect(resolvePromptTemplate("  hello $ARGUMENTS  ", "world")).toBe("hello world");
  });
});

describe("the shipped prompt commands", () => {
  it("register init and review, each saying what it does", () => {
    expect(PROMPT_COMMANDS.map((command) => command.name)).toEqual(["init", "review"]);
    expect(promptCommandFor("INIT")?.description).toBe("write a short AGENTS.md from commands that ran");
    expect(promptCommandFor("review")?.description).toContain("commit|branch|pr");
  });

  it("/init asks for a short file of proven commands, and no tour of the tree", () => {
    const template = promptCommandFor("init")!.template;
    // What has been measured about these files (arXiv 2602.11988): the model follows
    // them, a description of the layout does not help, and a longer file costs more.
    expect(template).toContain("write it only if it ran");
    expect(template).toContain("Do not survey the source tree");
    expect(template).toContain("under 60 lines");
    // Guidance for one directory is placed where it loads only there.
    expect(template).toContain(".agents/rules/<name>.md");
    expect(template).toContain("paths:");
    // The person's own focus comes before anything the template says to do.
    expect(template.indexOf("$ARGUMENTS")).toBeLessThan(template.indexOf("## Find out"));
    // The tool that asks the person is `ask_user`; the first version named one that
    // does not exist here.
    expect(template).toContain("`ask_user`");
    expect(template).not.toMatch(/`ask`/);
  });

  it("resolve their own arguments through the registry", () => {
    const input = promptCommandInput("/init focus on the GUI layer");
    expect(input?.command.name).toBe("init");
    expect(input?.args).toBe("focus on the GUI layer");
    const resolved = resolvePromptTemplate(input!.command.template, input!.args);
    expect(resolved).toContain("focus on the GUI layer");
    expect(resolved).toContain("AGENTS.md");
    expect(resolved).not.toContain("$ARGUMENTS");
  });

  it("match only a whole first token; anything else stays a plain message", () => {
    expect(promptCommandInput("/review main")).not.toBeNull();
    expect(promptCommandInput("/reviews main")).toBeNull();
    expect(promptCommandInput("/initiate")).toBeNull();
    expect(promptCommandInput("start /init")).toBeNull();
    expect(promptCommandInput("/")).toBeNull();
  });

  it("resolves a bare command with empty arguments", () => {
    const input = promptCommandInput("/review");
    expect(input?.args).toBe("");
    const resolved = resolvePromptTemplate(input!.command.template, input!.args);
    expect(resolved).toContain("Input: ");
    expect(resolved.startsWith("You are a code reviewer.")).toBe(true);
  });
});
