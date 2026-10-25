// A small XML reader for the parts of a workbook that say how it looks (styles, relationships,
// a table, a drawing). It runs in a worker, where there is no DOMParser. Namespace prefixes are
// dropped: `<x:font>` and `<font>` are the same element to everything that reads this.

export type XmlNode = { tag: string; attrs: Record<string, string>; kids: XmlNode[]; text: string };

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

export function unescapeXml(text: string): string {
  if (text.indexOf("&") < 0) return text;
  return text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, name: string) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[name] ?? whole;
  });
}

const local = (name: string) => {
  const colon = name.indexOf(":");
  return colon < 0 ? name : name.slice(colon + 1);
};

/** The attributes of one opening tag, as written between its name and its `>`. */
export function readAttrs(source: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const pattern = /([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  for (let match = pattern.exec(source); match; match = pattern.exec(source)) {
    attrs[local(match[1])] = unescapeXml(match[2] ?? match[3] ?? "");
  }
  return attrs;
}

/** The document as a tree of its elements, each with the text written directly in it. */
export function parseXml(source: string): XmlNode {
  const root: XmlNode = { tag: "", attrs: {}, kids: [], text: "" };
  const open: XmlNode[] = [root];
  const pattern = /<!--[\s\S]*?-->|<!\[CDATA\[([\s\S]*?)\]\]>|<\?[\s\S]*?\?>|<(\/?)([A-Za-z_][\w:.-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
  let last = 0;
  for (let match = pattern.exec(source); match; match = pattern.exec(source)) {
    const top = open[open.length - 1];
    if (match.index > last) top.text += unescapeXml(source.slice(last, match.index));
    last = pattern.lastIndex;
    if (match[1] !== undefined) {
      top.text += match[1];
      continue;
    }
    if (!match[3]) continue;
    if (match[2]) {
      if (open.length > 1) open.pop();
      continue;
    }
    const rest = match[4];
    const closed = rest.endsWith("/");
    const node: XmlNode = { tag: local(match[3]), attrs: readAttrs(rest), kids: [], text: "" };
    top.kids.push(node);
    if (!closed) open.push(node);
  }
  return root;
}

export const child = (node: XmlNode | undefined, tag: string): XmlNode | undefined => node?.kids.find((kid) => kid.tag === tag);
export const children = (node: XmlNode | undefined, tag: string): XmlNode[] => (node ? node.kids.filter((kid) => kid.tag === tag) : []);

/** The first element of that name anywhere under the node. */
export function find(node: XmlNode | undefined, tag: string): XmlNode | undefined {
  if (!node) return undefined;
  for (const kid of node.kids) {
    if (kid.tag === tag) return kid;
    const deeper = find(kid, tag);
    if (deeper) return deeper;
  }
  return undefined;
}

/** Every element of that name anywhere under the node, in document order. */
export function findAll(node: XmlNode | undefined, tag: string, into: XmlNode[] = []): XmlNode[] {
  if (!node) return into;
  for (const kid of node.kids) {
    if (kid.tag === tag) into.push(kid);
    findAll(kid, tag, into);
  }
  return into;
}
