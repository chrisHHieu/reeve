// One place that turns a model id into the name a person reads.
//
// The same model arrives as three spellings — curated "Gemini 3.7 Flash · Google",
// provider-prefixed "openrouter:google/gemini-3.7-flash", or bare "gemini-3.7-flash" —
// and each surface used to clean it up its own way (or not at all), so the header, the
// composer footer and the "Model switched" notice disagreed about what was running.
// Every caller now goes through here so they can't.
//
// The provider suffix is NOT noise: with bring-your-own-key the same model name is often
// served by two providers (OpenRouter's gemini-3.7-flash vs Google's), and a switch line
// or picker row that names only the model can't say which one it meant. `displayModel`
// is the bare name; `displayModelFull` is what surfaces show.

/** Families whose capitalization isn't just "first letter up". */
const KNOWN: Record<string, string> = {
  gpt: "GPT",
  glm: "GLM",
  ai: "AI",
  deepseek: "DeepSeek",
  minimax: "MiniMax",
};

/** Provider prefixes that expand beyond capitalization (the engine itself says "Vertex AI"). */
const PROVIDERS: Record<string, string> = {
  openrouter: "OpenRouter",
  "opencode-go": "OpenCode Go",
  vertex: "Vertex AI",
  vertexai: "Vertex AI",
  vertex_ai: "Vertex AI",
  openai: "OpenAI",
  together: "Together",
  fireworks: "Fireworks",
  moonshot: "Moonshot",
  zai: "Z.ai",
  "zhipuai-coding-plan": "Zhipu AI",
  "zai-coding-plan": "Z.AI Coding Plan",
};

function humanizeSegment(segment: string): string {
  return segment
    .split("-")
    .map((word) => {
      const known = KNOWN[word.toLowerCase()];
      if (known) return known;
      // Version runs ("3.7", "4.8") keep their dots; anything else gets a capital lead.
      return /^\d[\d.]*$/.test(word) ? word : word.charAt(0).toUpperCase() + word.slice(1);
    })
    .join(" ");
}

function providerSuffix(id: string): string {
  // The prefix before ":" is the provider; without one, the org before "/" is the closest
  // thing to a provider an id carries ("google/gemini-3.7-flash" is Google's own serving).
  const prefix = id.includes(":") ? id.split(":")[0] : id.includes("/") ? id.split("/")[0] : "";
  if (!prefix) return "";
  return PROVIDERS[prefix.toLowerCase()] || humanizeSegment(prefix);
}

/**
 * The provider a model id belongs to, as a person reads it: "deepseek:x" → "DeepSeek",
 * "vertexai:y" → "Vertex AI". Empty for an id that names no provider at all (the model
 * picker's provider control then falls back to the catalogue's title).
 */
export function displayProvider(id: string): string {
  return providerSuffix(id);
}

/**
 * The bare display name: the curated label's name half when the catalog has one, otherwise
 * the id stripped of its provider prefix, its org prefix and its hyphens —
 * "openrouter:google/gemini-3.7-flash" → "Gemini 3.7 Flash".
 */
export function displayModel(id: string, labels?: Record<string, string>): string {
  const curated = labels?.[id]?.split(" · ")[0];
  if (curated) return curated;
  const noProvider = id.includes(":") ? id.split(":").slice(1).join(":") : id;
  const noOrg = noProvider.split("/").pop() || noProvider;
  return humanizeSegment(noOrg);
}

/**
 * The full display name: bare name + " · Provider" so same-name models from different
 * providers are tellable apart. A curated label already carries its provider suffix and
 * passes through whole; a bare id with no provider or org prefix has nothing to add.
 */
export function displayModelFull(id: string, labels?: Record<string, string>): string {
  const curated = labels?.[id];
  if (curated) return curated;
  const name = displayModel(id, labels);
  const suffix = providerSuffix(id);
  return suffix ? `${name} · ${suffix}` : name;
}

/**
 * Rewrite the engine's "Model switched to <id>" notice so it names the model the same way
 * every other surface does — provider included, since the switch line is exactly where a
 * bare name misleads. Two shapes arrive here:
 *  - a pure id ("openrouter:google/gemini-3.7-flash") → cleaned and provider-suffixed;
 *  - a mixed form ("vertex:gemini/Gemini 3.7 Flash") — a provider prefix (and often an
 *    org prefix) glued to a name with spaces, which custom model strings carry. The
 *    prefixes come off, the name stays as written, the provider is said.
 * A notice that already reads as a friendly name passes through untouched — re-humanizing
 * words that aren't an id can only lose information.
 */
export function modelSwitchNoticeText(raw: string | undefined): string {
  const text = raw || "Model switched";
  const m = text.match(/^(.*\bswitched to\s+)(.+)$/i);
  if (!m) return text;
  const [, lead, rest] = m;
  if (!/\s/.test(rest) && /[:/]/.test(rest)) return lead + displayModelFull(rest);
  const mixed = rest.match(/^([A-Za-z][\w.-]*):\s*(?:[\w.-]+\/)?(.+)$/);
  if (mixed) {
    const provider = PROVIDERS[mixed[1].toLowerCase()] || humanizeSegment(mixed[1]);
    return `${lead}${mixed[2].trim()} · ${provider}`;
  }
  return text;
}
