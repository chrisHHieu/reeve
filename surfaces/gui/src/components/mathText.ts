/**
 * Make a model's math survive the markdown parser, without eating money.
 *
 * Two problems, both measured on 2026-09-08 against REAL output — 1,171 assistant messages
 * from this app's own session store — rather than against strings invented to prove a point.
 *
 * 1. DELIMITERS DIFFER BY VENDOR. `remark-math` understands `$…$` and `$$…$$` only, but
 *    OpenAI emits `\(…\)` / `\[…\]`. The native ChatGPT and Claude web apps normalize
 *    delimiters before rendering, which is why their own pages look right while an app
 *    talking to the same model over the API shows raw TeX. So we normalize too.
 *
 * 2. SINGLE-DOLLAR MATH EATS CURRENCY. `remark-math` ships `singleDollarTextMath: true` and
 *    its own docs warn that single dollars "often interfere with 'normal' dollars in text".
 *    This app reads Stripe, QuickBooks and TCBS, so that is not hypothetical. Running the
 *    real remark pipeline over 15 real paragraphs:
 *
 *        singleDollarTextMath: false  -> 5/15 wrong (every formula lost, incl. the bug
 *                                                    that started this: `$\rightarrow$`)
 *        singleDollarTextMath: true   -> 4/15 wrong (money read as formulas: "$9B → $30B",
 *                                                    "$2,500 – $3,500", "$20/thang, $8")
 *
 *    Neither is acceptable, so single-dollar stays ON and the currency dollars are escaped
 *    here instead. The trigger is narrow — a `$` directly followed by a DIGIT — because
 *    that is the whole damaging class: `$5`, `$1.2M`, `$20/month`.
 *
 * THE EXEMPTION IS NOT OPTIONAL. That trigger alone still got 1/15 wrong, and the failure
 * came out of the real corpus rather than imagination — an agent had written:
 *
 *     - $1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12 = \mathbf{78}$
 *
 * real math, opening on a digit. So a `$…$` span is spared when its body contains a
 * BACKSLASH, and the span then renders as the formula it is. With the exemption: 0/15.
 *
 * Why a backslash and nothing else. Wider "looks like maths" sets were measured against the
 * same corpus plus money written near maths-ish punctuation, and every one of them lost
 * ground:
 *
 *     `\` only     -> 1/11 wrong   (misses `$2^{n}$`, a formula with no TeX command)
 *     `\` `^`      -> 1/11 wrong   (now breaks "Ky quy $2 ^ $3")
 *     `\` `^` `_`  -> 2/11 wrong   (also breaks "File $1_backup va $2_final")
 *     `\` `^` `_` `=` -> 4/11 wrong (also breaks "Tong: $5 = $10 chia doi")
 *
 * A backslash between two dollar amounts is close to unambiguous; `=` between them is
 * ordinary arithmetic prose. So the known cost is `$2^{n}$` — a digit-leading formula using
 * no TeX command — rendering as text. Nothing in the real corpus looked like that.
 *
 * What is deliberately NOT done: a rule about `$LETTERS$`. It would fix a ticker written as
 * `$VCB$` and break `cho $n$ phần tử` in the same stroke — one mild wrongness traded for
 * another, plus a rule no reader could predict. Both render legibly today; the currency case
 * did not.
 */

// Fenced blocks (including one still being streamed, hence `|$`) and inline code spans.
// Split with a capturing group so the odd-indexed pieces are the code, left untouched:
// escaping a dollar inside `echo $5` would put a backslash on the user's screen. The real
// corpus needs this — an agent wrote "dòng 163 dùng `$65B` không có US".
const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g;

/** A TeX command inside the span — the one signal that survived measurement. */
const TEX_COMMAND = /\\/;

/**
 * Is this `$` opening a real formula rather than quoting a price?
 *
 * Looks ahead for the closing `$` and asks what is between them. The search stops at a
 * blank line because remark-math's inline math does too, so a lone `$5` cannot reach into
 * the next paragraph to find a partner and call itself maths.
 */
function opensFormula(after: string): boolean {
  const paragraphEnd = after.search(/\n[ \t]*\n/);
  const span = paragraphEnd === -1 ? after : after.slice(0, paragraphEnd);
  const close = span.indexOf("$");
  return close !== -1 && TEX_COMMAND.test(span.slice(0, close));
}

function transform(chunk: string): string {
  return (
    chunk
      // Currency first. Doing it after normalization would escape the `$` that
      // normalization had just produced, breaking `\(5\)`.
      //   (?<![\\$])  not already escaped, and not the second `$` of a `$$` opener
      //   (?=\d)      the damaging class, and only it
      .replace(/(?<![\\$])\$(?=\d)/g, (dollar, offset: number, whole: string) =>
        opensFormula(whole.slice(offset + 1)) ? dollar : "\\$",
      )
      .replace(/\\\(([\s\S]*?)\\\)/g, (_m, body: string) => `$${body}$`)
      .replace(/\\\[([\s\S]*?)\\\]/g, (_m, body: string) => `$$${body}$$`)
  );
}

/** Run `fn` over the prose only, leaving fenced blocks and inline code untouched. */
function outsideCode(text: string, fn: (chunk: string) => string): string {
  return text
    .split(CODE)
    .map((chunk, i) => (i % 2 === 1 ? chunk : fn(chunk)))
    .join("");
}

/** Normalize vendor math delimiters and protect currency, outside code. */
export function prepareMath(text: string): string {
  return outsideCode(text, transform);
}
