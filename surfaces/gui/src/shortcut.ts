// A keyboard shortcut as the person's own system writes it: ⌘B on a Mac, Ctrl+B elsewhere.
// A label only; the handlers themselves accept either modifier.
export function shortcut(key: string, platform: string = typeof navigator === "undefined" ? "" : navigator.platform): string {
  return /mac|iphone|ipad/i.test(platform) ? `⌘${key}` : `Ctrl+${key}`;
}
