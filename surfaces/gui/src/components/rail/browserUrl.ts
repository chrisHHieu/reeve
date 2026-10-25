// Address parsing for the Browser tab. The rail is a controlled iframe carrier, so the
// address box takes the shorthand a browser bar would ("localhost:5173", "example.com/x")
// and normalizes it to an explicit http(s) URL before the parent commits it to history.
// Everything after the host is left exactly as typed.

const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

// A colon that is followed by digits is a port, not a scheme — "localhost:3000",
// "example.com:8080". "file://", "javascript:" and "mailto:" are real schemes and get
// rejected before this check ever matters.
function isHostPort(value: string): boolean {
  const colon = value.indexOf(":");
  if (colon <= 0) return false;
  const host = value.slice(0, colon);
  const rest = value.slice(colon + 1);
  if (!/^\d/.test(rest)) return false;
  return host === "localhost" || host.includes(".") || /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
}

function isLocalHost(host: string): boolean {
  const bracketed = host.startsWith("[");
  const bare = bracketed
    ? host.slice(1, host.indexOf("]") === -1 ? undefined : host.indexOf("]"))
    : host.split(":")[0];
  const name = bare.toLowerCase();
  if (name === "localhost" || name === "::1" || name === "127.0.0.1" || name === "0.0.0.0") return true;
  if (name.endsWith(".local")) return true;
  if (name.startsWith("10.")) return true;
  if (name.startsWith("192.168.")) return true;
  return /^172\.(1[6-9]|2\d|3[01])\./.test(name);
}

export function normalizeAddress(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  // A space is never in an address; "not a url ::" was framed as https://not a url ::, a blank
  // page with no word about why (2026-10-11).
  if (/\s/.test(trimmed)) return null;

  const scheme = SCHEME_RE.exec(trimmed)?.[1]?.toLowerCase();
  if (scheme) {
    if (scheme === "http" || scheme === "https") return trimmed;
    if (!isHostPort(trimmed)) return null;
  }

  const host = trimmed.split(/[/?#]/, 1)[0];
  if (isLocalHost(host) || /:\d+$/.test(host)) return `http://${trimmed}`;
  // A bare word is a search, not a host: a public address names a domain.
  if (!host.includes(".")) return null;
  return `https://${trimmed}`;
}

/** An address on this computer or its local network: a dev server, never asked about first. */
export function isLocalUrl(url: string): boolean {
  try {
    return isLocalHost(new URL(url).host);
  } catch {
    return false;
  }
}

export function displayHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}
