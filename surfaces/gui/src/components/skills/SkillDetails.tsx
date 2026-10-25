import { useEffect } from "react";
import type { SkillRow } from "../../api";
import { Icon } from "../Icon";
import { usageLine } from "./usage";

/** One skill's details, in a sheet beside the list (the settings redesign, 2026-10-20): its
 *  instructions as the file has them, where it comes from, and what can be done with it. Escape or
 *  a press outside closes it. */
export function SkillDetails({
  row,
  installedFrom,
  armed,
  onClose,
  onEdit,
  onReveal,
  onDelete,
  onDisarm,
}: {
  row: SkillRow;
  installedFrom?: string;
  armed: boolean;
  onClose: () => void;
  onEdit?: () => void;
  onReveal?: () => void;
  onDelete?: () => void;
  onDisarm: () => void;
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const origin = row.compat ? "From a shared skills folder" : installedFrom ? `Installed from ${installedFrom}` : row.source === "local" ? "Yours" : row.source;
  return (
    <>
      <div className="skill-sheet-backdrop" data-floating onClick={onClose} />
      <aside className="skill-sheet" role="dialog" aria-label={row.name} data-testid="skill-details">
        <div className="skill-sheet-head">
          <h2 className="skill-sheet-title">{row.name}</h2>
          <button type="button" className="settings-icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div className="skill-sheet-facts">
          <span>{row.enabled ? "On everywhere" : "Off everywhere"}</span>
          <span>{origin}</span>
          <span>{usageLine(row)}</span>
        </div>
        {row.description && <p className="skill-sheet-desc">{row.description}</p>}
        <div className="skill-sheet-label">SKILL.md</div>
        <pre className="skill-sheet-body" data-testid="skill-details-body">
          {row.instructions || "No instructions."}
        </pre>
        <div className="skill-sheet-actions">
          {onEdit && (
            <button type="button" className="settings-text-btn" onClick={onEdit} data-testid="skill-details-edit">
              Edit
            </button>
          )}
          {onReveal && (
            <button type="button" className="settings-text-btn" onClick={onReveal}>
              <Icon name="folder" size={12} /> Show folder
            </button>
          )}
          <span className="flex-1" />
          {onDelete && (
            <button type="button" className="settings-text-btn danger" aria-label={`Delete ${row.name}`} title={installedFrom ? "Remove (uninstall)" : "Delete"} onClick={onDelete} onBlur={onDisarm}>
              {armed ? "Confirm delete" : installedFrom ? "Uninstall" : "Delete"}
            </button>
          )}
        </div>
      </aside>
    </>
  );
}
