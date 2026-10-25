import type { Page } from "@playwright/test";
import { eventSockets, framesSent, openedSessionSockets, sessionSockets, socketsBySession, type SessionChannel } from "./channels";
import type { MockState } from "./state";
import { playTurn } from "./turns";

// The sockets the app opens, played by a script: the app-wide event stream, each session's socket
// (the fake agent of turns.ts, and the frames that answer what it waits on), and the terminal.

/** The id at the end of a socket's path, before any query. */
const idIn = (url: string, path: string, fallback: string) => url.split(path)[1]?.split("?")[0] || fallback;

export async function scriptSockets(page: Page, s: MockState) {
  await page.routeWebSocket(/\/ws\/events$/, (ws) => {
    eventSockets.set(page, ws);
  });
  const appSend = (event: unknown) => eventSockets.get(page)?.send(JSON.stringify(event));

  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    const send = (type: string, data: Record<string, unknown> = {}) => ws.send(JSON.stringify({ type, data }));
    const channel: SessionChannel = { send, close: () => ws.close() };
    const sid = idIn(ws.url(), "/ws/session/", "sess-lead");
    sessionSockets.set(page, channel);
    openedSessionSockets.set(page, [...(openedSessionSockets.get(page) ?? []), sid]);
    if (!socketsBySession.get(page)) socketsBySession.set(page, new Map());
    socketsBySession.get(page)!.set(sid, channel);
    // The real `ready` names the session's mode (without it the composer showed its default label
    // and the Mode menu marked nothing), and says whether it runs in a temporary folder: false for
    // a draft that has not picked one, the record a later move must not apply again.
    send("ready", { mode: "workspace-write", temp_workspace: false });

    const waiting = { tool: "run_shell", fails: false };
    let stream: ReturnType<typeof setInterval> | null = null;
    // Once a message has been sent, a model change is a switch mid-session.
    let hadTurn = false;

    const approve = (decision: string) => {
      const denied = decision === "deny";
      if (waiting.tool === "run_shell" && waiting.fails) {
        // The engine's frame for a call that raised: `error`, with the failure's own text. A
        // failed call does not end the turn (the model reads it and tries something else), and
        // that open turn is what the failure specs look at, so no answer follows, nor on a deny.
        waiting.fails = false;
        if (denied) {
          send("tool_finished", { name: "run_shell", status: "denied" });
          send("assistant_message", { text: "Understood — skipped it." });
        } else {
          send("tool_finished", { name: "run_shell", status: "error", result_preview: '{"error": "1 failed, 3 passed"}' });
        }
        return;
      }
      if (denied) {
        send("tool_finished", { name: waiting.tool, status: "denied" });
        send("assistant_message", { text: waiting.tool === "run_shell" ? "Understood — skipped the command." : "Understood — skipped it." });
        send("turn_done");
        return;
      }
      if (waiting.tool === "run_shell") {
        // A command that prints as it works, streamed as `tool_output_delta` (harness/tools/output.py)
        // so the card fills before the result. The result comes a beat after the last line, the
        // window a live card is for; the turn ends with it, not here, or it would settle first.
        send("tool_output_delta", { call_id: "shell-1", text: "collecting\n" });
        send("tool_output_delta", { call_id: "shell-1", text: "README.md\n" });
        setTimeout(() => {
          send("tool_finished", {
            // "ok" is the status the engine sends (authorize.py). Anything else draws as an error.
            name: "run_shell", call_id: "shell-1", status: "ok", result_preview: "README.md", duration_ms: 1_200,
            presentation: {
              summary: "Command succeeded", kind: "execute",
              card: { card: "terminal", title: "ls", cwd: "/Users/test/Reeve", output: "README.md\n", exit_code: 0 },
            },
          });
          send("assistant_message", { text: "The command ran; 1 file found." });
          send("turn_done");
        }, 1_200);
        return;
      }
      send("tool_finished", { name: waiting.tool, status: "ok", result_preview: "ok" });
      // The decision is said back, so a spec can check what went over the wire (always_task and the like).
      send("assistant_message", { text: `Done via ${waiting.tool} [decision=${decision}]` });
      send("turn_done");
    };

    ws.onMessage((raw) => {
      const msg = JSON.parse(String(raw));
      framesSent.set(page, [...(framesSent.get(page) ?? []), msg]);
      switch (msg.type) {
        case "user_message":
          hadTurn = true;
          // A forced skill's turn starts with the line the person typed ("/name …") as `display`,
          // as the server's does, and the client matches its own bubble against it.
          send("turn_start", { input: msg.text, ...(msg.skill ? { display: `/${msg.skill}${msg.text ? ` ${msg.text}` : ""}` } : {}) });
          playTurn({
            text: msg.text, msg, sid, page, s, send, appSend,
            awaiting: (tool, opts) => {
              waiting.tool = tool;
              if (opts?.fails !== undefined) waiting.fails = opts.fails;
            },
            streaming: (timer) => { stream = timer; },
          });
          break;
        case "approval":
          approve(msg.decision);
          break;
        case "question_response":
          send("tool_finished", { name: "ask_user", status: "ok", call_id: "aq-ask", result_preview: JSON.stringify({ answer: msg.answer }) });
          send("assistant_message", { text: `Starting with: ${msg.answer}` });
          send("turn_done");
          break;
        case "plan_response": {
          const verdict = msg.approved ? { approved: true, ...(msg.mode ? { mode: msg.mode } : {}) } : { approved: false, feedback: msg.feedback || "" };
          send("tool_finished", { name: "propose_plan", status: "ok", call_id: "pp-plan", result_preview: JSON.stringify(verdict) });
          send("assistant_message", { text: msg.approved ? "Plan approved — implementing." : "Revising the plan." });
          send("turn_done");
          break;
        }
        case "tool_response":
          // Declined or not, the turn goes on: a missing tool makes the report say less, openly,
          // and never drops the check.
          send("assistant_message", {
            text: msg.approved ? "Installed gitleaks 8.30.1 — scanned history, no secrets found." : "Skipped gitleaks. Coverage: history secret sweep done by hand instead.",
          });
          send("turn_done");
          break;
        case "interrupt":
          // As the engine does: `interrupted` and no answer. Keeping what streamed is the client's.
          if (stream) clearInterval(stream);
          stream = null;
          send("interrupted", {});
          send("turn_done");
          break;
        case "set_model":
          // Every switch is said, as the server says it (session_socket._apply_model); only one
          // after a turn has the line the transcript keeps.
          send("model_changed", { model: msg.model, text: hadTurn ? `Model switched to ${msg.model}` : null });
          break;
        case "retry":
          // The engine runs the turn again with no new message: the turn's input is empty.
          send("turn_start", { input: "" });
          send("assistant_message", { text: "Recovered after retry." });
          send("turn_done");
          break;
      }
    });
  });

  // The person's terminal (`/terminal`): a shell played by the script, so the panel's attach,
  // type, stream and close all run. It is marked (harness/tools/shell_marks.py): the prompt and
  // each command carry their marks. `serve` keeps running, as a dev server does, until stopped.
  const mark = (data: string) => `\x1b]${data}\x07`;
  const PROMPT = `${mark("133;A")}PS> ${mark("133;B")}`;
  await page.routeWebSocket(/\/ws\/terminal\//, (ws) => {
    const send = (type: string, data: Record<string, unknown> = {}) => ws.send(JSON.stringify({ type, data }));
    const sid = idIn(ws.url(), "/ws/terminal/", "s1");
    send("terminal", {
      ok: true, session_id: sid, id: `user-${sid}`, cwd: "/Users/test/Reeve/launch-note", shell: "pwsh", running: true,
      marks: true, output: PROMPT, produced: PROMPT.length,
    });
    // xterm sends each key; the line is said back on Enter, as a shell would, so a spec reads one
    // line rather than a chunk a character.
    let line = "";
    let running = false;
    const enter = (text: string) => {
      if (text === "exit") return send("exit", { exit_code: 0 });
      const start = `\r\n${mark(`633;E;${text}`)}${mark("133;C")}`;
      if (text === "serve") {
        running = true;
        return send("output", { chunk: `${start}  Local:   http://localhost:5173/\r\n` });
      }
      const failed = text === "fail";
      const printed = failed ? "Error: the build failed\r\n" : `echo: ${text}\r\n`;
      send("output", { chunk: `${start}${printed}${mark(`133;D;${failed ? 1 : 0}`)}${PROMPT}` });
    };
    ws.onMessage((raw) => {
      const msg = JSON.parse(String(raw));
      if (msg.type === "input") {
        const data = String(msg.data || "");
        if (!/[\r\n]/.test(data)) {
          line += data;
          return;
        }
        const text = (line + data.replace(/[\r\n]/g, "")).trim();
        line = "";
        enter(text);
      } else if (msg.type === "interrupt") {
        const ended = running ? `${mark("133;D;130")}${PROMPT}` : "";
        running = false;
        send("output", { chunk: `^C\r\n${ended}` });
      }
    });
  });
}
