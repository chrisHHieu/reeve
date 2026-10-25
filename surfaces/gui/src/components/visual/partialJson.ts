// The arguments of a call still being written, read as far as they go. A visual
// draws its blocks as they arrive, the way Intelligent UI draws as the model generates: this
// closes whatever is open at the last complete value and parses that, so a block appears once
// its fields have landed and never from half a number or half a key.

/** The JSON value in `raw` up to its last complete member, or undefined when there is none yet. */
export function parsePartialJson(raw: string): unknown {
  if (!raw.trim()) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    // Fall through: the text is still arriving.
  }
  // Walk the text keeping the open containers, and remember the last place where cutting and
  // closing them would leave valid JSON: right after a complete value inside a container.
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  let safe = -1;
  let safeStack: string[] = [];
  let expectingKey = false;
  let afterKey = false;
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') {
        inString = false;
        // A string that was a value (not a key) is a complete member.
        if (!expectingKey || afterKey) {
          safe = i + 1;
          safeStack = stack.slice();
        }
        if (expectingKey && !afterKey) afterKey = true;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{" || ch === "[") {
      stack.push(ch === "{" ? "}" : "]");
      expectingKey = ch === "{";
      afterKey = false;
      if (ch === "[") {
        // An empty array is already a value.
        safe = i + 1;
        safeStack = stack.slice();
      }
      continue;
    }
    if (ch === "}" || ch === "]") {
      stack.pop();
      safe = i + 1;
      safeStack = stack.slice();
      const top = stack[stack.length - 1];
      expectingKey = top === "}";
      afterKey = top === "}";
      continue;
    }
    if (ch === ",") {
      const top = stack[stack.length - 1];
      expectingKey = top === "}";
      afterKey = false;
      continue;
    }
    if (ch === ":") {
      afterKey = true;
      continue;
    }
    if (/[\d\-tfn]/.test(ch)) {
      // A number or literal: complete once the next structural character comes.
      let j = i;
      while (j < raw.length && !/[,\]}\s]/.test(raw[j])) j += 1;
      if (j < raw.length) {
        safe = j;
        safeStack = stack.slice();
      }
      i = j - 1;
    }
  }
  if (safe < 0) return undefined;
  const text = raw.slice(0, safe).replace(/,\s*$/, "") + safeStack.slice().reverse().join("");
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
