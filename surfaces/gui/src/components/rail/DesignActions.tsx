// What a design page can do outside its preview, in the tab row beside its tab, as OpenDesign's file
// viewer has it (the owner's pick 2026-10-19): Present it (in the tab, full screen, or in a
// browser window), its History (the versions its checks kept), Export it (a PDF, a picture of the
// whole page, a zip, one HTML file, saved to Downloads), Share it (the same on the clipboard), and
// open it in an editor on this machine. Last, Lock: the version the app is built from.
//
// A PDF and a picture are drawn by Reeve's own browser at the width the person is looking at, and
// with the internet only when they let the page out, so the file is the page they see.
import { useEffect, useState } from "react";
import { exportDesign, getEditors, openInEditor, revealArtifact, type DesignExportKind, type Editor } from "../../api";
import { ActionMenu, type MenuEntry } from "./ActionMenu";
import { DesignLock } from "./DesignLock";
import { Icon } from "../Icon";

/** What the row says after it acted: a line, and the file it saved when it saved one. */
export interface DesignOutcome {
  text: string;
  saved?: string;
}

const KIND_WORDS: Record<DesignExportKind, string> = { pdf: "a PDF", png: "a picture", zip: "a zip", html: "one HTML file" };

let editorsAsked: Promise<Editor[]> | null = null;
/** The editors on this machine, asked once a run of the app. */
function editorsOnce(): Promise<Editor[]> {
  editorsAsked ??= getEditors().catch(() => []);
  return editorsAsked;
}

/** Put a `data:` PNG on the clipboard as a picture. */
export async function copyPicture(dataUrl: string): Promise<void> {
  const blob = await (await fetch(dataUrl)).blob();
  const Item = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem;
  if (!Item || !navigator.clipboard?.write) throw new Error("this window cannot put a picture on the clipboard");
  await navigator.clipboard.write([new Item({ "image/png": blob })]);
}

export function DesignActions({
  sessionId,
  path,
  width,
  internet,
  versions,
  versionsOpen,
  onToggleVersions,
  onPresent,
  onOutcome,
}: {
  sessionId: string;
  path: string;
  /** The width the page is drawn at now, in px. */
  width: number;
  internet: boolean;
  versions: number;
  versionsOpen: boolean;
  onToggleVersions: () => void;
  onPresent: (where: "tab" | "full") => void;
  onOutcome: (outcome: DesignOutcome) => void;
}) {
  const name = path.split(/[\\/]/).pop() || path;
  const [busy, setBusy] = useState<"export" | "share" | null>(null);
  const [editors, setEditors] = useState<Editor[]>([]);
  useEffect(() => {
    let alive = true;
    void editorsOnce().then((found) => alive && setEditors(found));
    return () => {
      alive = false;
    };
  }, []);
  const layout = Math.round(Math.min(2560, Math.max(320, width || 1440)));

  const save = async (kind: DesignExportKind) => {
    setBusy("export");
    try {
      const done = await exportDesign(sessionId, path, kind, { width: layout, internet }).catch(() => null);
      onOutcome(
        done?.ok && done.saved
          ? { text: `Saved ${done.name} to ${done.folder}.`, saved: done.saved }
          : { text: `${name} could not be saved as ${KIND_WORDS[kind]}: ${done?.error || "the server did not answer"}.` },
      );
    } finally {
      setBusy(null);
    }
  };

  const share = async (what: "picture" | "html" | "path") => {
    setBusy("share");
    try {
      if (what === "path") {
        await navigator.clipboard.writeText(path);
        onOutcome({ text: `Copied the path of ${name}.` });
        return;
      }
      const done = await exportDesign(sessionId, path, what === "picture" ? "png" : "html", {
        width: layout,
        internet,
        deliver: "data",
      }).catch(() => null);
      if (!done?.ok) {
        onOutcome({ text: `Nothing was copied: ${done?.error || "the server did not answer"}.` });
        return;
      }
      if (what === "picture" && done.png) await copyPicture(done.png);
      if (what === "html" && typeof done.html === "string") await navigator.clipboard.writeText(done.html);
      onOutcome({ text: what === "picture" ? `Copied a picture of ${name} at ${layout} px.` : `Copied ${name} as one HTML file.` });
    } catch (error) {
      onOutcome({ text: `Nothing was copied: ${error instanceof Error ? error.message : "the clipboard refused"}.` });
    } finally {
      setBusy(null);
    }
  };

  const present: MenuEntry[] = [
    { id: "tab", label: "In this tab", icon: "eye", onSelect: () => onPresent("tab") },
    { id: "full", label: "Full screen", icon: "play", onSelect: () => onPresent("full") },
    { id: "window", label: "In a browser window", icon: "externalLink", onSelect: () => void revealArtifact(sessionId, path, "open") },
  ];
  const exports: MenuEntry[] = [
    { id: "pdf", label: "Export as PDF", icon: "file", onSelect: () => void save("pdf") },
    { id: "png", label: "Export as image", icon: "image", onSelect: () => void save("png") },
    { id: "zip", label: "Download as .zip", icon: "archive", onSelect: () => void save("zip") },
    { id: "html", label: "Export as standalone HTML", icon: "fileCode", onSelect: () => void save("html") },
  ];
  const shares: MenuEntry[] = [
    { id: "picture", label: "Copy a picture", icon: "image", onSelect: () => void share("picture") },
    { id: "html", label: "Copy as one HTML file", icon: "fileCode", onSelect: () => void share("html") },
    { id: "path", label: "Copy its path", icon: "copy", onSelect: () => void share("path") },
  ];
  const opens: MenuEntry[] = [
    ...editors.map((editor) => ({
      id: editor.id,
      label: `Open in ${editor.name}`,
      icon: "codeXml" as const,
      onSelect: () =>
        void openInEditor(sessionId, path, editor.id).then((done) => {
          if (!done.ok) onOutcome({ text: `${editor.name} did not open: ${done.error || "no answer"}.` });
        }),
    })),
    ...(editors.length ? (["sep"] as MenuEntry[]) : []),
    { id: "folder", label: "Show in folder", icon: "folderOpen", onSelect: () => void revealArtifact(sessionId, path, "reveal") },
  ];

  return (
    <div className="design-actions" data-testid="design-actions">
      <ActionMenu label="Present" icon="monitorPlay" items={present} showLabel={false} className="am-soft" testid="design-present" />
      <button
        type="button"
        className={"am-btn am-soft" + (versionsOpen ? " on" : "")}
        aria-label={versions >= 2 ? `History, ${versions} versions` : "History"}
        aria-expanded={versionsOpen}
        title={versions >= 2 ? `History: ${versions} versions` : "History: no earlier version yet"}
        disabled={versions < 2}
        data-testid="design-versions-toggle"
        onClick={onToggleVersions}
      >
        <Icon name="history" size={15} />
      </button>
      <ActionMenu label="Export" icon="download" items={exports} className="am-dark" busy={busy === "export"} testid="design-export" />
      <ActionMenu label="Share" icon="forward" items={shares} className="am-outline" busy={busy === "share"} testid="design-share" />
      <ActionMenu
        label={editors.length ? `Open in ${editors[0].name}` : "Open"}
        icon="codeXml"
        items={opens}
        showLabel={false}
        caret
        testid="design-open"
      />
      <DesignLock sessionId={sessionId} path={path} version={versions} />
    </div>
  );
}
