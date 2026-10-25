// The formulas a what-if block recomputes as the person moves a slider. The grammar
// is the one `harness/tools/visual/formula.py` checks before the block is shown, so a formula
// that reaches the GUI has already been read once; this side compiles it to a closure over the
// current inputs and never calls `eval`.
//
//   expr  := term (("+" | "-") term)*
//   term  := unary (("*" | "/" | "%") unary)*
//   unary := "-" unary | power
//   power := atom (("**" | "^") unary)?
//   atom  := number | name | name "(" args ")" | "(" expr ")"

export type Scope = Record<string, number>;
export type Compiled = (scope: Scope) => number;

const FUNCTIONS: Record<string, (...args: number[]) => number> = {
  min: (...a) => Math.min(...a),
  max: (...a) => Math.max(...a),
  abs: (x) => Math.abs(x),
  round: (x, digits = 0) => {
    const f = 10 ** digits;
    return Math.round(x * f) / f;
  },
  floor: (x) => Math.floor(x),
  ceil: (x) => Math.ceil(x),
  sqrt: (x) => Math.sqrt(x),
  pow: (x, y) => x ** y,
  log: (x) => Math.log(x),
  exp: (x) => Math.exp(x),
  // Payment per period of a loan: rate per period, number of periods, principal.
  pmt: (rate, periods, principal) =>
    rate === 0 ? principal / periods : (principal * rate) / (1 - (1 + rate) ** -periods),
};

const TOKEN = /\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(\*\*|[-+*/%^(),]))/y;

type Token = { kind: "num" | "name" | "op"; value: string };

function tokens(text: string): Token[] {
  const out: Token[] = [];
  const source = text.trimEnd();
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < source.length) {
    const at = TOKEN.lastIndex;
    const match = TOKEN.exec(source);
    if (!match || TOKEN.lastIndex === at) throw new Error(`cannot read "${source.slice(at, at + 12)}"`);
    if (match[1] !== undefined) out.push({ kind: "num", value: match[1] });
    else if (match[2] !== undefined) out.push({ kind: "name", value: match[2] });
    else out.push({ kind: "op", value: match[3] });
  }
  return out;
}

/** The formula as a function of its names, or an Error saying where it is wrong. */
export function compile(formula: string | number): Compiled {
  if (typeof formula === "number") return () => formula;
  const list = tokens(String(formula));
  let i = 0;
  const peek = () => list[i];
  const take = (op: string) => {
    const token = list[i];
    if (token && token.kind === "op" && token.value === op) {
      i += 1;
      return true;
    }
    return false;
  };
  const expr = (): Compiled => {
    let left = term();
    for (;;) {
      if (take("+")) {
        const a = left, b = term();
        left = (s) => a(s) + b(s);
      } else if (take("-")) {
        const a = left, b = term();
        left = (s) => a(s) - b(s);
      } else return left;
    }
  };
  const term = (): Compiled => {
    let left = unary();
    for (;;) {
      if (take("*")) {
        const a = left, b = unary();
        left = (s) => a(s) * b(s);
      } else if (take("/")) {
        const a = left, b = unary();
        left = (s) => a(s) / b(s);
      } else if (take("%")) {
        const a = left, b = unary();
        left = (s) => a(s) % b(s);
      } else return left;
    }
  };
  const unary = (): Compiled => {
    if (take("-")) {
      const a = unary();
      return (s) => -a(s);
    }
    if (take("+")) return unary();
    const base = atom();
    if (take("**") || take("^")) {
      const exponent = unary();
      return (s) => base(s) ** exponent(s);
    }
    return base;
  };
  const atom = (): Compiled => {
    const token = peek();
    if (!token) throw new Error("ends too early");
    i += 1;
    if (token.kind === "num") {
      const value = Number(token.value);
      return () => value;
    }
    if (token.kind === "name") {
      if (take("(")) {
        const fn = FUNCTIONS[token.value];
        if (!fn) throw new Error(`unknown function ${token.value}()`);
        const args: Compiled[] = [];
        if (!take(")")) {
          for (;;) {
            args.push(expr());
            if (take(")")) break;
            if (!take(",")) throw new Error(`expected "," or ")" in ${token.value}()`);
          }
        }
        return (s) => fn(...args.map((arg) => arg(s)));
      }
      const name = token.value;
      return (s) => {
        const value = s[name];
        if (value === undefined) throw new Error(`unknown name "${name}"`);
        return value;
      };
    }
    if (token.value === "(") {
      const inner = expr();
      if (!take(")")) throw new Error('a "(" is not closed');
      return inner;
    }
    throw new Error(`unexpected "${token.value}"`);
  };
  const compiled = expr();
  if (i < list.length) throw new Error(`unexpected "${list[i].value}"`);
  return compiled;
}

/** A formula's value, or NaN when it cannot be computed (a name not yet defined while a block is
 *  still streaming in, a division by zero). A block draws NaN as "—", never as a number. */
export function evaluate(formula: string | number, scope: Scope): number {
  try {
    const value = compile(formula)(scope);
    return Number.isFinite(value) ? value : NaN;
  } catch {
    return NaN;
  }
}
