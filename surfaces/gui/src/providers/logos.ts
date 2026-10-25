// The providers' marks for the provider gallery. Most are taken from the
// MIT-licensed lobe-icons set; BytePlus's is the mark from its own website, used with permission.
// They ship inside the app like the connectors' (no CDN at run time). The keys are /v1/providers
// names; a name with no mark here gets a neutral letter in the gallery.

import anthropic from "./logos/anthropic.svg";
import bedrock from "./logos/bedrock.svg";
import byteplus from "./logos/byteplus.svg";
import deepseek from "./logos/deepseek.svg";
import fireworks from "./logos/fireworks.svg";
import gemini from "./logos/gemini.svg";
import kimi from "./logos/kimi.svg";
import meta from "./logos/meta.svg";
import minimax from "./logos/minimax.svg";
import mistral from "./logos/mistral.svg";
import ollama from "./logos/ollama.svg";
import openai from "./logos/openai.svg";
import opencodeGo from "./logos/opencode-go.svg";
import openrouter from "./logos/openrouter.svg";
import qwen from "./logos/qwen.svg";
import together from "./logos/together.svg";
import vertex from "./logos/vertex.svg";
import volcengine from "./logos/volcengine.svg";
import xai from "./logos/xai.svg";
import zai from "./logos/zai.svg";

// The gallery's order: the names people know first, the long tail below the fold. Each entry is
// [provider, mark]. A subscription route wears its vendor's mark, since it is the same models paid
// for another way (owner, 2026-08-21: no bare letter), and both GLM Coding Plans wear Z.ai's, which
// is whose models they are.
const GALLERY: [string, string][] = [
  ["anthropic", anthropic],
  ["anthropic-subscription", anthropic],
  ["openai", openai],
  ["openai-codex", openai],
  ["gemini", gemini],
  ["ark", byteplus],
  ["ark-agent-plan-cn", volcengine],
  ["meta", meta],
  ["ollama", ollama],
  ["bedrock", bedrock],
  ["vertex", vertex],
  ["openrouter", openrouter],
  ["opencode-go", opencodeGo],
  ["fireworks", fireworks],
  ["together", together],
  ["zai", zai],
  ["zhipuai-coding-plan", zai],
  ["zai-coding-plan", zai],
  ["kimi", kimi],
  ["deepseek", deepseek],
  ["mistral", mistral],
  ["qwen", qwen],
  ["minimax", minimax],
  ["xai", xai],
];

export const PROVIDER_LOGOS: Record<string, string> = Object.fromEntries(GALLERY);

export const PROVIDER_ORDER = GALLERY.map(([name]) => name);

/** Where a provider sits in the gallery; one it does not list comes after all the rest. */
export function providerRank(name: string): number {
  const at = PROVIDER_ORDER.indexOf(name);
  return at < 0 ? PROVIDER_ORDER.length : at;
}
