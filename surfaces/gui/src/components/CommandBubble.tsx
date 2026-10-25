// A message sent with a slash command: the command's name highlighted, its line, and
// what the person typed after it. The text the model read, an expanded template of seventy lines,
// is behind "What Reeve sent", folded: it is what Reeve wrote, not what the person said.
//
// A skill draws the same way in its own colour, with its mark. What the model read for a skill is
// one line of framing around the person's words, so there is nothing more to open.

import { useState } from "react";
import type { SentCommand, UserItem } from "../types";
import { Disclosure } from "../ui/Disclosure";
import { Icon } from "./Icon";
import { CopyButton } from "./primitives/CopyButton";

/** The line the person wrote, as they wrote it: "/init keep it short". */
export function commandLine(command: SentCommand): string {
  return `/${command.name}${command.args ? ` ${command.args}` : ""}`;
}

export function CommandBubble({ item }: { item: UserItem }) {
  const command = item.command!;
  const [open, setOpen] = useState(false);
  const skill = command.kind === "skill";
  const sent = skill ? "" : item.text;
  const lines = sent ? sent.split("\n").length : 0;
  return (
    <div className="cmd-bubble" data-testid="command-bubble" data-kind={skill ? "skill" : "prompt"}>
      <div className="cmd-head">
        <span className="cmd-name" data-testid="command-name">
          {skill && <Icon name="sparkle" size={12} />}/{command.name}
        </span>
        {command.description && (
          <span className="cmd-desc">{command.description.charAt(0).toUpperCase() + command.description.slice(1)}</span>
        )}
        {command.source && <span className="cmd-source">{command.source}</span>}
      </div>
      {command.args && <div className="cmd-args">{command.args}</div>}
      {sent && (
        <>
          <button
            type="button"
            className="cmd-sent-toggle"
            aria-expanded={open}
            data-testid="command-sent-toggle"
            onClick={() => setOpen((value) => !value)}
          >
            What Reeve sent
            <Icon name="chevronRight" size={12} className={"cmd-chevron" + (open ? " open" : "")} />
          </button>
          <Disclosure open={open}>
            <div className="cmd-sent" data-testid="command-sent">
              <pre>{sent}</pre>
              <div className="cmd-sent-foot">
                <span>
                  {lines} line{lines === 1 ? "" : "s"} · the instructions the agent got
                </span>
                <CopyButton text={sent} />
              </div>
            </div>
          </Disclosure>
        </>
      )}
    </div>
  );
}
