// The card vocabulary, mirroring `harness/tools/cards.py`.
//
// Snake_case on purpose, and the one place in this GUI where that is true. The reason is
// that these objects cross the wire UNMAPPED — the same way the `_display` sidecar already
// does (`_display.hidden_by_filters` is read verbatim in itemsFromMessages.ts) — and a
// rename layer for a nested structure is a third thing to keep in step with two others.
// Everywhere else, where the GUI owns the shape, it is camelCase as usual.
//
// `card` is the discriminant, which is what makes the union open: an unknown `card` value
// arriving from a newer engine narrows to nothing, and the row falls back to its plain
// text. That is a designed outcome, not an error path — see `cardModels.ts`.

/** One file's change. `old_text === null` means no prior content was available. */
export interface FileDiff {
  path: string;
  old_text: string | null;
  new_text: string;
  /**
   * Exact churn, when the engine could compute it.
   *
   * Absent for a diff derived from call arguments (a replacement's `old`/`new`, a write's
   * content), where counting each side IS the true count. Present for a context-windowed
   * hunk, where it is not: unchanged lines appear in both sides, so a surface counting them
   * separately would report a one-line edit as `+7 −7`.
   */
  added?: number;
  /** 1-based line where the old/new side's window starts in its file. Absent for a diff
   *  derived from call arguments, whose position in the file is unknown: such a diff shows
   *  no line numbers rather than numbers counted from 1 (GUI tools review, B2). */
  old_start?: number;
  new_start?: number;
  removed?: number;
}

export interface FileLocation {
  path: string;
  line?: number;
}

export interface GenericCallCard {
  card: "generic";
  title: string;
  kind?: string;
  raw_input?: unknown;
  content?: string[];
  locations?: FileLocation[];
}

export interface TerminalCallCard {
  card: "terminal";
  title: string;
  description?: string;
  cwd?: string;
}

export interface DiffCallCard {
  card: "diff";
  title: string;
  diffs: FileDiff[];
  locations?: FileLocation[];
}

export type CallCard = GenericCallCard | TerminalCallCard | DiffCallCard;

export interface GenericResultCard {
  card: "generic";
  title?: string;
  content?: string[];
}

export interface TerminalResultCard {
  card: "terminal";
  title?: string;
  output?: string;
  exit_code?: number;
  signal?: string;
  cwd?: string;
  truncated?: boolean;
  /** How many lines the retention budget dropped, when the tool could count them. */
  omitted_lines?: number;
  /** The shell it ran in ("powershell", "pwsh", "bash", "cmd"); absent on older results. */
  shell?: string;
}

export interface DiffResultCard {
  card: "diff";
  title?: string;
  diffs: FileDiff[];
}

export interface SearchLineMatch {
  line_number: number;
  line: string;
}

export interface SearchFileGroup {
  path: string;
  matches: SearchLineMatch[];
}

export interface SearchMatchesCard {
  card: "search";
  shape: "matches";
  title?: string;
  files: SearchFileGroup[];
  truncated: boolean;
  total: number;
}

export interface SearchPathsCard {
  card: "search";
  shape: "paths";
  title?: string;
  paths: string[];
  truncated: boolean;
  total: number;
}

export type SearchResultCard = SearchMatchesCard | SearchPathsCard;

export interface ReadLine {
  number: number;
  text: string;
}

export interface ReadResultCard {
  card: "read";
  title?: string;
  path: string;
  offset: number;
  lines: ReadLine[];
  total_lines: number;
  lang?: string;
}

export interface WebSource {
  url: string;
  title?: string;
  snippet?: string;
  published_at?: string;
  /** The byline, when the provider reports one. */
  author?: string;
  /** The site's mark is kept on this computer (`GET /v1/favicons/{host}`). */
  icon?: boolean;
}

export interface WebSearchCard {
  card: "web";
  kind: "search";
  title?: string;
  sources: WebSource[];
  answer?: string;
  /** The provider the results came from (duckduckgo, tavily…), shown as a badge. */
  provider?: string;
  /** The query the search ran, for highlighting terms in the answer and snippets. */
  query?: string;
  /** The service expected to answer and did not (`exa`), and why: the provider above is
   *  then the fallback, and the card says so. */
  fallback_from?: string;
  fallback_reason?: string;
  /** The searches merged into this one, when there were several. */
  queries?: string[];
  /** How many results the domain / date filters removed. */
  filtered_out?: number;
  /** How many of the merged queries failed. */
  failed_queries?: number;
  truncated: boolean;
}

/** How a fetch read the page (`harness/integrations/web`): its start, an answer a model
 *  wrote from it, the places a phrase occurs, or what came back instead of text. */
export type WebFetchMode = "page" | "answer" | "find" | "redirect" | "image" | "pdf";

export interface WebFetchCard {
  card: "web";
  kind: "fetch";
  /** The page's own `<title>`. */
  title?: string;
  url: string;
  status_code?: number;
  truncated?: boolean;
  /** A bounded preview of the text the model got (for `markdown`, already cleaned: no images,
   *  no link targets). */
  excerpt?: string;
  excerpt_kind?: "markdown" | "text" | "code";
  /** The length of the text the model got. */
  chars?: number;
  /** Absent on a card from before the field: read as `page`. */
  mode?: WebFetchMode;
  /** Where that text sits in the page. */
  read?: { start?: number; end?: number; page_chars?: number };
  /** How it was read: each key only when true. */
  about?: {
    left_out_chars?: number;
    cached_seconds?: number;
    plain_http?: boolean;
    source_cut?: boolean;
    js_required?: boolean;
    unanswered?: string;
  };
  answer?: {
    question?: string;
    text: string;
    by?: string;
    quotes: { quote: string; found: boolean }[];
    quotes_total?: number;
    quotes_found?: number;
    parts?: number;
    unread_chars?: number;
  };
  found?: { phrase: string; count: number; matches: { at: number; text: string }[] };
  redirect?: { host?: string; url?: string };
}

export type WebResultCard = WebSearchCard | WebFetchCard;

/** A loaded skill's body, so the transcript can render it as markdown rather than a dump. */
export interface SkillResultCard {
  card: "skill";
  name: string;
  description?: string;
  instructions?: string;
}

/** One file a turn handed over: the `present` tool's own declaration.
 *
 *  `description` is the model's words about the file, which is what the card's second
 *  line shows — it is the only thing on that line that code cannot derive (the reference design's
 *  `PresentedFile`, `packages/deliverables/tool-present/src/types.ts`). It is optional
 *  because a model that declared a file without explaining it must still get a card, and
 *  it is `null`-able because the engine sends an explicit `null` for an absent one — a
 *  distinction the surface does not care about (both read as "not explained"), kept here
 *  so the type matches the bytes rather than an assumption about them. */
export interface PresentedFileEntry {
  path: string;
  description?: string | null;
}

/** The files a turn declared as deliverables (`present`), in the order it declared them. */
export interface FilesResultCard {
  card: "files";
  title?: string;
  files: PresentedFileEntry[];
}

/** What a fold of the model's own context did (`fold_context`), in numbers. The note is the
 *  call's own argument, so the card does not repeat it. */
export interface FoldResultCard {
  card: "fold";
  folded_messages: number;
  kept_messages: number;
  before_tokens: number;
  after_tokens: number;
  saved_tokens: number;
  /** The note stands in for the earlier notes instead of joining them. */
  replaced_notes?: boolean;
}

export type ResultCard =
  | GenericResultCard
  | TerminalResultCard
  | DiffResultCard
  | ReadResultCard
  | SearchResultCard
  | WebResultCard
  | SkillResultCard
  | FilesResultCard
  | FoldResultCard;

/** The presentation payload: a sentence, a category, and optionally a card. */
export interface Presented {
  summary: string;
  detail?: string;
  kind?: string;
  link?: string;
  card?: CallCard | ResultCard;
}
