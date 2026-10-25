// The model's own markdown extensions: `:file-citation{…}` and `:code-comment{…}`
// directives. They are NOT markdown, so they never reach the renderer as text — they are
// parsed out here, one citation becomes a compact chip in place, and the review comments are
// collected for the card list that the answer renders underneath.
//
// Everything is tolerant on purpose: this is model output, so an attribute may be missing,
// quoted with smart quotes, or a directive may be half-written while streaming (`hideOpenTail`
// is what keeps that tail off screen instead of printing `:code-comment{title="…` at the end
// of a sentence).

export interface FileCitation {
  path: string;
  /** What the model says the file has to do with the answer — the `purpose`. */
  purpose?: string;
}

export type CommentPriority = "high" | "medium" | "low";

export interface CodeComment {
  title: string;
  body: string;
  file: string;
  /** 1-based, as a reader counts lines. */
  start?: number;
  end?: number;
  priority?: CommentPriority;
}

/** `:name{…}` — the braces hold `key="value"` pairs, possibly with smart quotes. */
const DIRECTIVE = /:([a-z][a-z-]*)\{([^{}]*)\}/g;

/** The last, still-open directive while text streams: `… :code-comment{title="half`. */
const OPEN_TAIL = /:[a-z][a-z-]*\{[^{}]*$/;

function attributes(raw: string): Record<string, string> {
  const out: Record<string, string> = {};
  const pair = /([a-z_]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|“([^”]*)”|([^,\s]+))/gi;
  let match: RegExpExecArray | null;
  while ((match = pair.exec(raw)) !== null) {
    out[match[1].toLowerCase()] = match[2] ?? match[3] ?? match[4] ?? match[5] ?? "";
  }
  return out;
}

function number(value: string | undefined): number | undefined {
  if (value === undefined) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** Drop an unclosed directive so a streamed answer does not print half a call. */
export function hideOpenTail(text: string): string {
  const tail = OPEN_TAIL.exec(text);
  return tail ? text.slice(0, tail.index) : text;
}

export function parseDirectives(text: string): {
  /** The prose with citations rewritten to `file:` links and comments removed. */
  text: string;
  citations: FileCitation[];
  comments: CodeComment[];
} {
  const citations: FileCitation[] = [];
  const comments: CodeComment[] = [];
  const rewritten = text.replace(DIRECTIVE, (whole, name: string, raw: string) => {
    const attrs = attributes(raw);
    if (name === "file-citation" && attrs.path) {
      const citation: FileCitation = {
        path: attrs.path,
        ...(attrs.purpose ? { purpose: attrs.purpose } : {}),
      };
      citations.push(citation);
      const leaf = citation.path.replace(/\\/g, "/").split("/").pop() || citation.path;
      const title = citation.purpose ? ` "${citation.purpose.replace(/"/g, "'")}"` : "";
      // In place, as a compact link: the reader keeps both the file and the sentence.
      return `[${leaf}](file:${citation.path}${title})`;
    }
    if (name === "code-comment" && (attrs.title || attrs.body)) {
      const priority = attrs.priority?.toLowerCase();
      comments.push({
        title: attrs.title ?? "",
        body: attrs.body ?? "",
        file: attrs.file ?? "",
        ...(number(attrs.start) !== undefined ? { start: number(attrs.start)! } : {}),
        ...(number(attrs.end) !== undefined ? { end: number(attrs.end)! } : {}),
        ...(priority === "high" || priority === "medium" || priority === "low"
          ? { priority }
          : {}),
      });
      return "";
    }
    return whole;
  });
  return { text: rewritten, citations, comments };
}

/** The review comments in a message, for the card list the answer renders underneath. */
export function codeCommentsOf(text: string): CodeComment[] {
  return parseDirectives(text).comments;
}
