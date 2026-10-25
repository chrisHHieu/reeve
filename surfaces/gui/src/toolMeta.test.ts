import { describe, expect, it } from "vitest";
import { HIDDEN_TOOLS, groupKey, groupMeta, isHiddenTool, toolMeta } from "./toolMeta";

describe("toolMeta", () => {
  it("maps every built-in family to its icon and word", () => {
    expect(toolMeta("run_shell").label).toBe("Shell");
    expect(toolMeta("run_code").label).toBe("Program");
    expect(toolMeta("grep").icon).toBe("search");
    expect(toolMeta("read_file").label).toBe("Read");
    expect(toolMeta("write_file").icon).toBe("pencilLine");
    expect(toolMeta("list_files").icon).toBe("folderTree");
    expect(toolMeta("git_diff").icon).toBe("gitCompare");
    expect(toolMeta("git_status").icon).toBe("branch");
    expect(toolMeta("todo_write").label).toBe("Plan");
    expect(toolMeta("terminal_send").label).toBe("Shell");
  });

  it("maps the families the first pass left as Tool/Working", () => {
    for (const name of ["replace_in_file", "apply_patch", "apply_unified_diff"]) {
      expect(toolMeta(name).label).toBe("Edit");
      expect(toolMeta(name).running).toBe("Editing");
    }
    expect(toolMeta("task").label).toBe("Task");
    // One mark per family (owner, 2026-10-12): a subagent is the helper, not a search.
    expect(toolMeta("task").icon).toBe("bot");
    for (const name of ["remember", "memory_read", "memory_search", "memory_update", "memory_forget"]) {
      expect(toolMeta(name).label).toBe("Memory");
      // The brain is the Thought rows'; memory is the notebook.
      expect(toolMeta(name).icon).toBe("notebookPen");
    }
    expect(toolMeta("save_skill").label).toBe("Skill");
    expect(toolMeta("save_skill").icon).not.toBe(toolMeta("load_skill").icon);
    expect(toolMeta("web_fetch").icon).not.toBe(toolMeta("web_search").icon);
    expect(toolMeta("search_history").icon).not.toBe(toolMeta("create_scheduled_task").icon);
    expect(toolMeta("sleep_until").icon).not.toBe(toolMeta("create_scheduled_task").icon);
    for (const name of [
      "create_scheduled_task",
      "list_scheduled_tasks",
      "update_scheduled_task",
      "delete_scheduled_task",
    ])
      expect(toolMeta(name).label).toBe("Schedule");
    for (const name of ["sleep_until", "wake_on", "wake_on_event"]) expect(toolMeta(name).label).toBe("Wake");
    for (const name of ["propose_plan", "update_plan"]) expect(toolMeta(name).label).toBe("Plan");
    for (const name of ["send_message", "send_file"]) expect(toolMeta(name).label).toBe("Send");
    for (const name of [
      "subscribe_channel",
      "unsubscribe_channel",
      "list_subscriptions",
      "get_channel_messages",
    ])
      expect(toolMeta(name).label).toBe("Channels");
    expect(toolMeta("request_directory").label).toBe("Request");
  });

  it("folds the shell detached-run lifecycle, but never a run of task calls", () => {
    expect(groupKey("run_shell")).toBe("shell");
    expect(groupKey("shell_task_output")).toBe("shell");
    expect(groupKey("shell_task_kill")).toBe("shell");
    // The reference design renders each child as its own task card (`task-tool.tsx`), so consecutive
    // `task` calls must not share a grouping key: a fan-out is separate rows, one openable
    // card per child — never one family header hiding them.
    expect([groupKey("task"), groupKey("task"), groupKey("task")]).toEqual([
      null,
      null,
      null,
    ]);
    expect(groupKey("write_file")).toBeNull();
    expect(groupMeta("shell").label).toBe("Shell");
  });

  it("knows which rows its own card supersedes", () => {
    for (const name of [
      "todo_write",
      "update_plan",
      "ask_user",
      "request_directory",
      "request_tool",
    ])
      expect(isHiddenTool(name)).toBe(true);
    // `propose_plan` is NOT suppressed: its content is a step in the transcript; only its
    // decision moved to the composer dock.
    expect(isHiddenTool("propose_plan")).toBe(false);
    expect(HIDDEN_TOOLS.has("write_file")).toBe(false);
  });

  it("carries the running gerund for the verb → noun swap", () => {
    expect(toolMeta("run_shell").running).toBe("Running");
    expect(toolMeta("read_file").running).toBe("Reading");
    expect(toolMeta("write_file").running).toBe("Editing");
    expect(toolMeta("grep").running).toBe("Searching");
    expect(toolMeta("task").running).toBe("Delegating");
    expect(toolMeta("run_code").running).toBe("Executing");
    expect(toolMeta("git_diff").running).toBe("Inspecting");
    expect(toolMeta("search_history").running).toBe("Recalling");
    // A server's row is titled by the server in every state: the shimmer says it runs.
    expect(toolMeta("mcp__wealthlog__x").running).toBe("Wealthlog");
    expect(toolMeta("brand_new_tool").running).toBe("Working");
  });

  it("mcp tools read as their server's name", () => {
    const m = toolMeta("mcp__wealthlog__get_portfolio");
    expect(m.icon).toBe("plug");
    expect(m.label).toBe("Wealthlog");
  });

  it("an unknown tool still gets a sane default, never a hole", () => {
    expect(toolMeta("brand_new_tool").icon).toBe("wrench");
    expect(toolMeta("brand_new_tool").label).toBe("Tool");
  });
});

describe("toolMeta done words", () => {
  it("every tool that has a running gerund has a past tense, like Thinking → Thought", () => {
    const names = ["run_shell", "run_code", "grep", "read_file", "list_files", "write_file",
      "replace_in_file", "apply_patch", "git_status", "task", "ask_user", "load_skill",
      "search_history", "web_search", "web_fetch", "lsp", "goal", "tool_search", "remember",
      "save_skill", "create_scheduled_task", "sleep_until", "send_message", "get_channel_messages",
      "create_item", "terminal_open", "some_unmapped_tool"];
    for (const name of names) {
      const meta = toolMeta(name);
      expect(meta.done, name).toBeTruthy();
      expect(meta.done, name).not.toBe(meta.running);
    }
    expect(toolMeta("write_file").done).toBe("Edited");
    expect(toolMeta("grep").done).toBe("Searched");
    expect(toolMeta("read_file").done).toBe("Read");
    expect(toolMeta("mcp__wealthlog__x").done).toBe("Wealthlog");
  });
});
