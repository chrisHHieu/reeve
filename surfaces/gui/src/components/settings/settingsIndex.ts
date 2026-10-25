import type { IconName } from "../Icon";

export type SettingsTab =
  | "general"
  | "appearance"
  | "instructions"
  | "models"
  | "advanced"
  | "remote"
  | "skills"
  | "plugins"
  | "subagents"
  | "memory"
  | "websearch"
  | "connectors";

/** Tabs that were merged into another (2026-10-10 redesign): a link that still names one opens
 * the page that holds it now, at the row it meant. */
export const SETTINGS_TAB_ALIASES: Record<string, { tab: SettingsTab; anchor: string }> = {
  voice: { tab: "advanced", anchor: "Voice input" },
  context: { tab: "advanced", anchor: "PDF attachments" },
};

/** The tab a caller asked for, with a merged tab's name resolved to where it lives now. */
export function resolveSettingsTab(wanted: string | undefined): { tab: SettingsTab | undefined; anchor: string | null } {
  if (!wanted) return { tab: undefined, anchor: null };
  const alias = SETTINGS_TAB_ALIASES[wanted];
  return alias ? { tab: alias.tab, anchor: alias.anchor } : { tab: wanted as SettingsTab, anchor: null };
}

/** "more" holds what most people never open (Advanced): unlabelled, at the foot of the rail. */
export type SettingsGroupId = "app" | "agent" | "connections" | "more";

export const SETTINGS_GROUP_LABELS: Record<SettingsGroupId, string> = {
  app: "App",
  agent: "Agent",
  connections: "Connections",
  more: "",
};

export type SettingsIndexEntry = {
  key: SettingsTab;
  label: string;
  sub: string;
  icon: IconName;
  group: SettingsGroupId;
  keywords: string[];
};

export const SETTINGS_INDEX: SettingsIndexEntry[] = [
  {
    key: "general",
    label: "General",
    sub: "How Reeve behaves on this computer.",
    icon: "sliders",
    group: "app",
    keywords: [
      "Permissions",
      "Permission",
      "New conversations",
      "Default mode",
      "Full access",
      "Visuals",
      "Charts",
      "Visuals in answers",
      "Map background",
      "Maps",
      "Conversations",
      "Open tools when they finish",
      "Expand",
      "Collapse",
      "Tool rows",
      "Files",
      "Conversations folder",
      "Trusted workspaces",
      "This computer",
      "Open at login",
      "When you close the window",
      "Tray",
      "Keep this computer awake",
      "Updates",
      "Check for updates",
      "First-run setup",
      "Run setup again",
    ],
  },
  {
    key: "appearance",
    label: "Appearance",
    sub: "Theme, fonts and sizes on this computer.",
    icon: "palette",
    group: "app",
    keywords: [
      "Appearance",
      "Mode",
      "Light",
      "Dark",
      "Theme",
      "Palette",
      "Reeve",
      "Harbor",
      "Catppuccin",
      "Nord",
      "Gruvbox",
      "Solarized",
      "Tokyo Night",
      "GitHub",
      "Cupertino",
      "Fonts",
      "UI font",
      "Code font",
      "Terminal font",
      "Text size",
      "Code size",
      "Ligatures",
      "Cursor",
    ],
  },
  {
    key: "models",
    label: "Models",
    sub: "The providers Reeve can use, and the models offered in the composer. Keys stay on this computer.",
    icon: "sparkle",
    group: "agent",
    keywords: ["Providers", "API keys", "Connected", "Composer picker", "Default model", "Endpoint", "Model"],
  },
  {
    key: "instructions",
    label: "Instructions",
    sub: "What Reeve follows in every conversation.",
    icon: "scrollText",
    group: "agent",
    keywords: ["Global instructions", "SOUL.md", "Your instructions", "Instruction file"],
  },
  {
    key: "memory",
    label: "Memory",
    sub: "What Reeve keeps about each project between conversations. Everything kept is listed here.",
    icon: "notebookPen",
    group: "agent",
    keywords: ["Auto memory", "Saved about you earlier"],
  },
  {
    key: "skills",
    label: "Skills",
    sub: "Instructions Reeve loads when a task needs them. Turned off here, a skill is off everywhere.",
    icon: "book",
    group: "agent",
    keywords: ["Installed skills", "Add skill", "Import skill", "Built-in skills"],
  },
  {
    key: "subagents",
    label: "Subagents",
    sub: "Helpers Reeve can hand a task to. Saved as markdown, picked up by the next conversation.",
    icon: "bot",
    group: "agent",
    keywords: ["Built-in subagents", "Global subagents", "New subagent", "Model", "Tools"],
  },
  {
    key: "plugins",
    label: "Plugins",
    sub: "Bundles of skills, commands and subagents. Claude Code plugins load as they are; a plugin never grants a permission.",
    icon: "puzzle",
    group: "agent",
    keywords: ["Plugins", "Claude Code plugin", "Marketplace", "Plugin errors", "Commands"],
  },
  {
    key: "connectors",
    label: "Connectors",
    sub: "Apps and tools Reeve can use. Connected ones come first.",
    icon: "plug",
    group: "connections",
    keywords: ["MCP servers", "Add custom server", "Connected apps", "Tools", "Tool loading", "Search MCP tools on demand"],
  },
  {
    key: "websearch",
    label: "Web search",
    sub: "Where web searches go, and the key they use. Keys stay on this computer.",
    icon: "globe",
    group: "connections",
    keywords: ["Exa", "DuckDuckGo", "Search engine", "Exa account", "Exa API key", "Test search", "More search tools"],
  },
  {
    key: "remote",
    label: "Remote hosts",
    sub: "Computers a conversation can work on over SSH.",
    icon: "server",
    group: "connections",
    keywords: ["SSH hosts", "New host", "Identity file", "Remote environment"],
  },
  {
    key: "advanced",
    label: "Advanced",
    sub: "Attachments and voice input. The defaults suit most people.",
    icon: "wrench",
    group: "more",
    keywords: [
      "PDF attachments",
      "Token savings",
      "PDF fallback",
      "Extract text",
      "Send page images",
      "Max pages",
      "Max size",
      "Voice input",
      "Whisper model",
      "Microphone test",
      "Download model",
    ],
  },
];

export type SettingsSearchHit = {
  entry: SettingsIndexEntry;
  anchor: string | null;
};

export function searchSettings(index: SettingsIndexEntry[], query: string): SettingsSearchHit[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return index.map((entry) => ({ entry, anchor: null }));
  const hits: SettingsSearchHit[] = [];
  for (const entry of index) {
    if (entry.label.toLowerCase().includes(needle)) {
      hits.push({ entry, anchor: null });
      continue;
    }
    const keyword = entry.keywords.find((candidate) => candidate.toLowerCase().includes(needle));
    if (keyword) hits.push({ entry, anchor: keyword });
  }
  return hits;
}
