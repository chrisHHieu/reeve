// What a map is drawn on (Settings > Visuals): the online basemap (streets, places and terrain from
// OpenFreeMap, OpenStreetMap data), or the bundled country outlines alone, with nothing fetched.
// Per device, like the appearance it follows, so it lives in localStorage beside the theme
// (`theme.ts`); the requests it makes are listed in NOTICE.md.

import { useEffect, useState } from "react";

export type MapBasemap = "online" | "offline";

const KEY = "reeve:map-basemap";
const EVENT = "reeve:map-basemap-changed";

export function getMapBasemap(): MapBasemap {
  try {
    return localStorage.getItem(KEY) === "offline" ? "offline" : "online";
  } catch {
    return "online";
  }
}

export function setMapBasemap(value: MapBasemap) {
  try {
    if (value === "online") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, value);
  } catch {
    /* private mode: still applies until the page reloads */
  }
  window.dispatchEvent(new CustomEvent(EVENT));
}

export function useMapBasemap(): [MapBasemap, (value: MapBasemap) => void] {
  const [value, setValue] = useState<MapBasemap>(getMapBasemap);
  useEffect(() => {
    const sync = () => setValue(getMapBasemap());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return [value, setMapBasemap];
}

/** Whether the app is drawn dark now (`data-theme` on <html>, set by `theme.ts`). */
export function useDarkTheme(): boolean {
  const read = () => document.documentElement.dataset.theme === "dark";
  const [dark, setDark] = useState(read);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => setDark(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}
