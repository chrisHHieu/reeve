import { useEffect, useState } from "react";
import { getMemorySettings, type MemorySettings } from "../api";
import { AutoMemoryCard } from "./AutoMemoryCard";

// Settings ▸ Memory: each project's notes, as the files they are, and then what an
// earlier version saved about the person, for as long as anything of it is left. Nothing saves
// about the person any more, so there is no switch for it: what holds in every project is the
// Instructions file they write. The old notes are listed until they have moved what they want and
// deleted the rest; no conversation loads them.
export function MemorySection(_props: { model?: string } = {}) {
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  useEffect(() => {
    getMemorySettings()
      .then(setSettings)
      .catch(() => setSettings(null));
  }, []);
  if (!settings) return <div className="text-[13px] text-muted">Loading…</div>;
  return (
    <section>
      <AutoMemoryCard settings={settings} onSettings={setSettings} />
      {/* Empty once the old notes are gone, and then it draws nothing. */}
      <AutoMemoryCard scope="user" settings={settings} onSettings={setSettings} />
    </section>
  );
}
