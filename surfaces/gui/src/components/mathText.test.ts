import { describe, expect, it } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { prepareMath } from "./mathText";

/**
 * The measurement, not an illustration.
 *
 * Every string in CORPUS was mined from this app's own session store
 * (`%APPDATA%/Reeve/conversations`, 102 sessions / 1,171 assistant messages) by pulling
 * every paragraph containing a `$` followed by a digit. They are what our models actually
 * wrote to this user, not strings invented to make a rule look good — which matters,
 * because the corpus immediately contradicted the rule that shipped: case `sum` below is
 * real MATH that opens on a digit, and the plain "escape `$` before a digit" rule broke it.
 *
 * The assertions run the real remark pipeline and look at the mdast, because that is where
 * the damage happens. A rendered-DOM check would confuse "no formula here" with "a formula
 * that failed to draw".
 */
const proc = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkMath, { singleDollarTextMath: true });

/** What remark-math would treat as maths in this text. */
function mathIn(text: string): string[] {
  const found: string[] = [];
  const walk = (n: any) => {
    if (n.type === "inlineMath" || n.type === "math") found.push(n.value);
    (n.children ?? []).forEach(walk);
  };
  walk(proc.runSync(proc.parse(text)) as any);
  return found;
}

/** [name, real text, what a correct renderer should treat as maths] */
const CORPUS: Array<[string, string, string[]]> = [
  // --- money. none of it is maths. -------------------------------------------------
  [
    "salary range",
    "### 3. Yếu Tố Giúp Anh Đạt Mức Lương Ở Dải Cao (Top Range $2,500 – $3,500+)",
    [],
  ],
  ["saas pricing", "Gói Pro giá $20/tháng, add-on $8. Doanh thu từ $1.2M lên $3.4M.", []],
  [
    "funding rounds",
    "Số liệu chính xác hơn rồi ($9B → $30B → $47B → $65B; định giá $965B). Giờ mình tìm ảnh.",
    [],
  ],
  [
    "valuations",
    "Doanh thu thường niên hóa: Anthropic **$74,3 tỷ** vs OpenAI **$41,3 tỷ**. Anthropic định giá riêng tư $965 tỷ; OpenAI vừa gọi vòng $122 tỷ tại định giá $852 tỷ.",
    [],
  ],
  [
    "monthly spend",
    "* **Ghi chú chi tiêu tháng 8:** tăng lên **$784.85 USD** do 2 khoản gộp lớn: học tiếng Anh 3 tháng (**$172.31 USD** / 4.5M VND) và biếu gia đình (**$229.74 USD** / 6.0M VND).",
    [],
  ],
  [
    "net worth in VND",
    "* **Tài sản ròng mới (Net Worth):** `55.858.714` ➔ **55.635.001 VND** *(~$2,130.29 USD)*",
    [],
  ],
  [
    "earnings in prose",
    "Saved the briefing — five bullets covering Nvidia's record $96.2B earnings and the US-Venezuela oil deal.",
    [],
  ],
  [
    "amount inside a code span",
    "Khớp chính xác rồi (dòng 163 dùng `$65B` không có US). Thay lại đúng text.",
    [],
  ],
  [
    "interest coverage",
    "- Hệ số bao phủ lãi vay đạt **9.47 lần**, lợi nhuận áp đảo chi phí trả lãi ($919M).",
    [],
  ],

  // --- maths. all of it must survive. ----------------------------------------------
  // The bug that started the whole thing.
  ["arrow", String.raw`Timeline $\rightarrow$ Stack $\rightarrow$ Track.`, ["\\rightarrow", "\\rightarrow"]],
  // The counterexample the corpus produced: real maths opening on a digit.
  [
    "sum",
    String.raw`- $1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12 = \mathbf{78}$`,
    [String.raw`1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12 = \mathbf{78}`],
  ],
  ["famous formula", "Công thức $E = mc^2$ nổi tiếng.", ["E = mc^2"]],
  ["single letter", "cho $n$ phần tử", ["n"]],
  // OpenAI's delimiters, which remark-math does not know at all.
  ["paren delimiters", String.raw`Diện tích là \(\pi r^2\) chính xác.`, [String.raw`\pi r^2`]],
  ["bracket delimiters", String.raw`\[\sum_{i=1}^{n} x_i\]`, [String.raw`\sum_{i=1}^{n} x_i`]],
];

describe("prepareMath over the real corpus", () => {
  it.each(CORPUS)("%s", (_name, text, expected) => {
    expect(mathIn(prepareMath(text))).toEqual(expected);
  });

  it("is what makes the difference — the raw text fails on its own", () => {
    // The guard against a vacuous suite. If prepareMath were the identity function every
    // case above could still pass by accident; these two say the corpus really is hostile.
    const money = CORPUS.find(([n]) => n === "funding rounds")![1];
    expect(mathIn(money).length).toBeGreaterThan(0); // untreated, money reads as maths
    const parens = CORPUS.find(([n]) => n === "paren delimiters")![1];
    expect(mathIn(parens)).toEqual([]); // untreated, a real formula is invisible
  });

  it("turning single-dollar OFF is not the alternative", () => {
    // The other obvious fix, measured: it silences the money and loses every formula
    // written with single dollars, including the reported bug.
    const off = unified()
      .use(remarkParse)
      .use(remarkGfm)
      .use(remarkMath, { singleDollarTextMath: false });
    const mathOff = (t: string) => {
      const found: string[] = [];
      const walk = (n: any) => {
        if (n.type === "inlineMath" || n.type === "math") found.push(n.value);
        (n.children ?? []).forEach(walk);
      };
      walk(off.runSync(off.parse(t)) as any);
      return found;
    };
    expect(mathOff(String.raw`Timeline $\rightarrow$ Stack`)).toEqual([]);
    expect(mathOff("Công thức $E = mc^2$ nổi tiếng.")).toEqual([]);
  });
});

/**
 * The narrow rules, stated as rules. The corpus proves the outcome; these pin the edges a
 * corpus of one user's chats cannot be expected to contain.
 */
describe("prepareMath rules", () => {
  it("does not double-escape a dollar the model already escaped", () => {
    expect(prepareMath("costs \\$5 today")).toBe("costs \\$5 today");
  });

  it("keeps block math whose body starts with a digit", () => {
    // `$$5x$$` — the second `$` is followed by a digit, so a naive rule would escape it and
    // break the block it was opening.
    expect(prepareMath("$$5x + 1$$")).toBe("$$5x + 1$$");
  });

  it("will not pair two dollars across a paragraph break", () => {
    // Two prices in two different paragraphs are two prices. remark-math's inline maths
    // stops at a blank line, so the lookahead must too, or the first price would find a
    // partner in the next paragraph and call itself a formula.
    //
    // The backslash sits in the SECOND paragraph on purpose. Put it anywhere else and the
    // test passes whether or not the boundary is respected — which is what the first
    // version of this test did, and only a mutation showed it.
    expect(prepareMath("Giá $5 một tháng.\n\nCòn \\alpha rồi $9 thôi.")).toBe(
      "Giá \\$5 một tháng.\n\nCòn \\alpha rồi \\$9 thôi.",
    );
  });

  it("never touches a fenced code block", () => {
    const src = "```sh\necho $5\nprice=\\(x\\)\n```";
    expect(prepareMath(src)).toBe(src);
  });

  it("leaves an unterminated fence alone while it is still streaming", () => {
    // half a code block is the normal state mid-stream; escaping inside it would show a
    // backslash that then has to disappear when the fence closes
    const src = "```sh\necho $5\nnext line";
    expect(prepareMath(src)).toBe(src);
  });

  it("still protects prose that follows a closed fence", () => {
    expect(prepareMath("```\necho $5\n```\nthen $9 and $3")).toBe(
      "```\necho $5\n```\nthen \\$9 and \\$3",
    );
  });

  it("is a no-op on text with no math and no money", () => {
    const src = "# Heading\n\nA list:\n\n- one\n- two\n";
    expect(prepareMath(src)).toBe(src);
  });

  it("has a known cost, and this is it", () => {
    // A formula that opens on a digit and uses no TeX command reads as money and is
    // escaped. Measured against the alternatives: admitting `^` as a maths signal rescues
    // this one case and breaks real money ("Ký quỹ $2 ^ $3"), and admitting `=` breaks more
    // ("Tổng: $5 = $10 chia đôi"). The narrow rule is the one that loses least.
    expect(prepareMath("Kết quả $2^{n}$ phần tử.")).toBe("Kết quả \\$2^{n}$ phần tử.");
  });
});
