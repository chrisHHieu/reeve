// Incomplete-markdown repair for the STREAMING path.
//
// A model writes `**bold` for a few hundred milliseconds before the closing `**` arrives, and
// an unterminated fence swallows everything after it until the model closes it. Rendering the
// raw text means the answer visibly flickers: a paragraph turns into bold, the rest of the
// message jumps into a code block, then back. The reference design solves this with Streamdown's remend; we
// use the same library directly, on the same terms (it is the same code that powers
// Streamdown's `parseIncompleteMarkdown`).
//
// It runs on the text that already went through `prepareMath`, which is the same order the reference design
// uses (escape non-math `$`, then repair): by the time remend sees `$5` it is `\$5`, so the
// single-dollar completion cannot turn a price into a formula.
//
// Never called on a SETTLED message. remend completes what LOOKS open; on finished text that
// is exactly wrong.

import remend from "remend";

export function repairStreamingMarkdown(text: string): string {
  try {
    return remend(text, {
      // Single-dollar math is enabled in the parser (mathText.ts), so an open `$x` is worth
      // completing — the money that would make this dangerous was escaped before it got here.
      inlineKatex: true,
      // `~` is not strikethrough in this app's GFM options, so it must not be "escaped" into
      // a backslash the parser then shows literally.
      singleTilde: false,
      // An incomplete link shows its text; a `streamdown:incomplete-link` placeholder would
      // reach our `a` renderer and render as a dead anchor.
      linkMode: "text-only",
    });
  } catch {
    // Repair is a nicety; the raw text always renders.
    return text;
  }
}
