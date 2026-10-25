import type { SkillInstallInfo, SkillRow } from "../../api";
import { Icon } from "../Icon";

/** The tags after a skill's name in the list: where it came from, and anything that changes how the
 *  agent or the person can use it. */
export function SkillTags({ row, install, allShared }: { row: SkillRow; install?: SkillInstallInfo; allShared: boolean }) {
  return (
    <>
      {row.source !== "local" && <span className="settings-tag">{row.source}</span>}
      {install && (
        // From skills-lock.json: "installed from owner/repo".
        <span className="settings-tag" title={`Installed from ${install.source} (${install.sourceType}) — the delete button removes the copy and its install entry`}>
          {install.source}
        </span>
      )}
      {row.compat && !allShared && (
        <span className="settings-tag inline-flex items-center gap-1" title="From a shared skills folder — managed there. Copy it to your skills to make it yours.">
          <Icon name="folder" size={10} />
          shared folder
        </span>
      )}
      {!(row.description ?? "").trim() && (
        // It still loads when asked for by name, but the model's menu never shows it: the person has
        // to know why it is never picked.
        <span className="settings-tag settings-tag-warn" title="The agent's menu skips skills without a description — add one so it can find this skill on its own">
          hidden from the model — no description
        </span>
      )}
      {row.model_invocable === false && (
        // `disable-model-invocation`: the agent never picks it; the person can, from "/".
        <span className="settings-tag" title="Run this one yourself — the agent won't pick it on its own. Use / in a conversation.">
          user-only
        </span>
      )}
      {row.user_invocable === false && (
        // `user-invocable: false`: the agent's alone; "/" and running it by force are off for it.
        <span className="settings-tag" title="The agent uses this one on its own — it can't be run from a conversation.">
          model-only
        </span>
      )}
      {/* A skill that brings files must not look like a one-file one. */}
      {row.files ? (
        <span className="settings-tag inline-flex items-center gap-1">
          <Icon name="folder" size={10} /> {row.files.toLocaleString()} file{row.files === 1 ? "" : "s"}
        </span>
      ) : null}
    </>
  );
}
