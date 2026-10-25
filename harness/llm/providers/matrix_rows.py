"""The rows of the curated model matrix: the models we suggest, label and vouch for.

Each row is `(full routed id, picker label, capabilities, context window)`. The id is exactly what
the ProviderRouter receives, reseller namespaces included (``together:zai-org/GLM-5.2``), and a bare
id routes to the OpenAI default. The window is in tokens and feeds the context meter and the
compaction trigger; None means the vendor's figure was not checked, and the meter then hides and
shows no invented denominator.

The list is deliberately short: current-generation models that can call tools. A person can still
add any custom id, which falls back to the cautious heuristics in ``capabilities.py``. Reseller
rows rename with every model generation and need refreshing when their catalogues rotate, and where
a published catalogue disagrees with a row here, the catalogue wins at runtime (see ``matrix.py``).
Groq is not listed yet; it comes with its descriptor once that provider surface has been tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import ModelCapabilities

# What a row can do. The native three (OpenAI, Anthropic, Gemini) take PDFs inline, while the
# OpenAI-compatible vendors and resellers have no inline file part, so those fall back through
# ``pdf_support.py`` and the presets that serve them say ``pdf=False``.
TEXT = ModelCapabilities(tools=True, vision=False, parallel_tool_calls=True, streaming=True)
VISION = ModelCapabilities(tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True)
VISION_NO_PDF = ModelCapabilities(tools=True, vision=True, pdf=False, parallel_tool_calls=True, streaming=True)
# Gemini also takes audio and video. A model not listed here gets those only when a catalogue says so.
MULTIMODAL = ModelCapabilities(
    tools=True, vision=True, pdf=True, audio=True, video=True, parallel_tool_calls=True, streaming=True
)
# Verified on Converse: asked for two tool calls it emits them one at a time.
SERIAL_TOOLS = ModelCapabilities(tools=True, vision=False, parallel_tool_calls=False, streaming=True)


@dataclass(frozen=True)
class ModelEntry:
    label: str  # the picker's display name, e.g. "GLM-5.2 · via Together"
    caps: ModelCapabilities = TEXT
    context_window: Optional[int] = None  # tokens on the prompt side; None = not verified


_ROWS: tuple[tuple[str, str, ModelCapabilities, Optional[int]], ...] = (
    # -- OpenAI, addressed by bare ids
    ('gpt-5.6-sol', 'GPT-5.6 Sol · OpenAI', VISION, 400_000),
    ('gpt-5.6-terra', 'GPT-5.6 Terra · OpenAI', VISION, 400_000),
    ('gpt-5.6-luna', 'GPT-5.6 Luna · OpenAI', VISION, 400_000),
    ('gpt-5.5', 'GPT-5.5 · OpenAI', VISION, 400_000),
    # -- the ChatGPT subscription backend (sign-in, no API key)
    ('openai-codex:gpt-5.6-sol', 'GPT-5.6 Sol · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.6-terra', 'GPT-5.6 Terra · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.6-luna', 'GPT-5.6 Luna · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.2-codex', 'GPT-5.2 Codex · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.2', 'GPT-5.2 · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.1-codex', 'GPT-5.1 Codex · ChatGPT plan', VISION_NO_PDF, 400_000),
    ('openai-codex:gpt-5.1-codex-mini', 'GPT-5.1 Codex Mini · ChatGPT plan', TEXT, 400_000),
    # -- Anthropic
    ('anthropic:claude-fable-5', 'Claude Fable 5 · Anthropic', VISION, 1_000_000),
    ('anthropic:claude-opus-4-8', 'Claude Opus 4.8 · Anthropic', VISION, 200_000),
    ('anthropic:claude-sonnet-4-6', 'Claude Sonnet 4.6 · Anthropic', VISION, 200_000),
    ('anthropic:claude-haiku-4-5', 'Claude Haiku 4.5 · Anthropic', VISION, 200_000),
    # -- Claude subscription backend (sign-in, no API key)
    ('anthropic-subscription:claude-fable-5', 'Claude Fable 5 · Claude plan', VISION, 1_000_000),
    ('anthropic-subscription:claude-opus-4-8', 'Claude Opus 4.8 · Claude plan', VISION, 200_000),
    ('anthropic-subscription:claude-sonnet-4-6', 'Claude Sonnet 4.6 · Claude plan', VISION, 200_000),
    ('anthropic-subscription:claude-haiku-4-5', 'Claude Haiku 4.5 · Claude plan', VISION, 200_000),
    # -- Google Gemini
    ('gemini:gemini-3.1-pro-preview', 'Gemini 3.1 Pro · Google', MULTIMODAL, 1_048_576),
    ('gemini:gemini-3.6-flash', 'Gemini 3.6 Flash · Google', MULTIMODAL, 1_048_576),
    ('gemini:gemini-2.5-pro', 'Gemini 2.5 Pro · Google', MULTIMODAL, 1_048_576),
    ('gemini:gemini-2.5-flash', 'Gemini 2.5 Flash · Google', MULTIMODAL, 1_048_576),
    # -- BytePlus Ark (pay as you go)
    ('ark:dola-seed-evolving-latest-version', 'Dola Seed Evolving · BytePlus Ark', TEXT, 256_000),
    ('ark:dola-seed-2-1-turbo-260628', 'Dola Seed 2.1 Turbo · BytePlus Ark', TEXT, 256_000),
    # -- Volcengine Agent Plan, a separate endpoint, credential and catalog from Ark
    ('ark-agent-plan-cn:doubao-seed-evolving', 'Doubao Seed Evolving · Volcengine Agent Plan', TEXT, 256_000),
    ('ark-agent-plan-cn:doubao-seed-2.1-turbo', 'Doubao Seed 2.1 Turbo · Volcengine Agent Plan', TEXT, 256_000),
    # -- Meta
    ('meta:muse-spark-1.1', 'Muse Spark 1.1 · Meta', VISION_NO_PDF, None),
    # -- Z AI
    ('zai:glm-5.2', 'GLM-5.2 · Z AI', TEXT, 128_000),
    # -- Zhipu Coding Plan, a subscription endpoint apart from `zai`
    ('zhipuai-coding-plan:glm-5.3', 'GLM-5.3 · Zhipu Coding Plan', TEXT, 1_000_000),
    ('zhipuai-coding-plan:glm-5.3-flash', 'GLM-5.3-Flash · Zhipu Coding Plan', VISION_NO_PDF, 1_000_000),
    ('zhipuai-coding-plan:glm-5.3-highspeed', 'GLM-5.3 Highspeed · Zhipu Coding Plan', TEXT, 1_000_000),
    ('zhipuai-coding-plan:glm-4.6v', 'GLM-4.6V · Zhipu Coding Plan', VISION_NO_PDF, 128_000),
    ('zhipuai-coding-plan:glm-5.2', 'GLM-5.2 · Zhipu Coding Plan', TEXT, 1_000_000),
    ('zhipuai-coding-plan:glm-5.2-highspeed', 'GLM-5.2 Highspeed · Zhipu Coding Plan', TEXT, 1_000_000),
    ('zhipuai-coding-plan:glm-5-turbo', 'GLM-5-Turbo · Zhipu Coding Plan', TEXT, 200_000),
    ('zhipuai-coding-plan:glm-5.1', 'GLM-5.1 · Zhipu Coding Plan', TEXT, 200_000),
    ('zhipuai-coding-plan:glm-5v-turbo', 'GLM-5V-Turbo · Zhipu Coding Plan', VISION_NO_PDF, 200_000),
    ('zhipuai-coding-plan:glm-4.7', 'GLM-4.7 · Zhipu Coding Plan', TEXT, 204_800),
    # -- Z.AI Coding Plan, the international sibling of the Zhipu plan
    ('zai-coding-plan:glm-5.3', 'GLM-5.3 · Z.AI Coding Plan', TEXT, 1_000_000),
    ('zai-coding-plan:glm-5.3-flash', 'GLM-5.3-Flash · Z.AI Coding Plan', VISION_NO_PDF, 1_000_000),
    ('zai-coding-plan:glm-5.3-highspeed', 'GLM-5.3 Highspeed · Z.AI Coding Plan', TEXT, 1_000_000),
    ('zai-coding-plan:glm-5.2', 'GLM-5.2 · Z.AI Coding Plan', TEXT, 1_000_000),
    ('zai-coding-plan:glm-5.2-highspeed', 'GLM-5.2 Highspeed · Z.AI Coding Plan', TEXT, 1_000_000),
    ('zai-coding-plan:glm-5-turbo', 'GLM-5-Turbo · Z.AI Coding Plan', TEXT, 200_000),
    ('zai-coding-plan:glm-4.7', 'GLM-4.7 · Z.AI Coding Plan', TEXT, 204_800),
    # -- DeepSeek
    ('deepseek:deepseek-v4-flash', 'DeepSeek V4 Flash · DeepSeek', TEXT, 128_000),
    ('deepseek:deepseek-v4-pro', 'DeepSeek V4 Pro · DeepSeek', TEXT, 128_000),
    # -- Moonshot
    ('kimi:kimi-k2.6', 'Kimi K2.6 · Moonshot', TEXT, 256_000),
    # -- MiniMax
    ('minimax:MiniMax-M2.5', 'MiniMax M2.5 · MiniMax', TEXT, None),
    # -- Alibaba Qwen
    ('qwen:qwen3-max', 'Qwen3 Max · Alibaba', TEXT, 256_000),
    # -- xAI
    ('xai:grok-4.3', 'Grok 4.3 · xAI', TEXT, 256_000),
    # -- Mistral
    ('mistral:mistral-large-latest', 'Mistral Large · Mistral', TEXT, 128_000),
    # -- Together, whose namespaces are kept verbatim
    ('together:thinkingmachines/Inkling', 'Inkling · via Together', TEXT, None),
    ('together:zai-org/GLM-5.2', 'GLM-5.2 · via Together', TEXT, 128_000),
    ('together:moonshotai/Kimi-K3', 'Kimi K3 · via Together', VISION_NO_PDF, 1_000_000),
    ('together:moonshotai/Kimi-K2.7-Code', 'Kimi K2.7 Code · via Together', TEXT, 256_000),
    ('together:moonshotai/Kimi-K2.6', 'Kimi K2.6 · via Together', TEXT, 256_000),
    ('together:deepseek-ai/DeepSeek-V4-Pro', 'DeepSeek V4 Pro · via Together', TEXT, 128_000),
    ('together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8', 'Llama 4 Maverick · via Together', TEXT, 1_000_000),
    # -- Fireworks
    ('fireworks:accounts/fireworks/models/glm-5p2', 'GLM-5.2 · via Fireworks', TEXT, 128_000),
    ('fireworks:accounts/fireworks/models/kimi-k2p6', 'Kimi K2.6 · via Fireworks', TEXT, 256_000),
    ('fireworks:accounts/fireworks/models/deepseek-v4-pro', 'DeepSeek V4 Pro · via Fireworks', TEXT, 128_000),
    ('fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic', 'Llama 4 Maverick · via Fireworks', TEXT, 1_000_000),
    # -- OpenRouter: lowercase `<lab>/<model>` slugs, one key for all labs
    ('openrouter:z-ai/glm-5.2', 'GLM-5.2 · via OpenRouter', TEXT, 1_048_576),
    ('openrouter:moonshotai/kimi-k2.6', 'Kimi K2.6 · via OpenRouter', VISION_NO_PDF, 262_144),
    ('openrouter:deepseek/deepseek-v4-pro', 'DeepSeek V4 Pro · via OpenRouter', TEXT, 1_048_576),
    ('openrouter:meta-llama/llama-4-maverick', 'Llama 4 Maverick · via OpenRouter', VISION_NO_PDF, 1_048_576),
    # -- AWS Bedrock; `claude/` rides the native Anthropic path, `other/` the Converse API
    ('bedrock:claude/anthropic.claude-sonnet-4-6-v1:0', 'Claude Sonnet 4.6 · AWS Bedrock', VISION, 200_000),
    ('bedrock:claude/anthropic.claude-haiku-4-5-v1:0', 'Claude Haiku 4.5 · AWS Bedrock', VISION, 200_000),
    ('bedrock:other/amazon.nova-2-pro-v1:0', 'Nova 2 Pro · AWS Bedrock', TEXT, 300_000),
    ('bedrock:other/meta.llama4-maverick-17b-instruct-v1:0', 'Llama 4 Maverick · AWS Bedrock', TEXT, 1_000_000),
    ('bedrock:other/mistral.mistral-large-3-v1:0', 'Mistral Large 3 · AWS Bedrock', TEXT, 128_000),
    ('bedrock:other/nvidia.nemotron-super-3-120b', 'Nemotron Super 3 120B · AWS Bedrock', SERIAL_TOOLS, None),
    # -- Google Vertex AI; `gemini/` and `claude/` are native paths, `openweight/` the MaaS endpoint
    ('vertex:gemini/gemini-3.7-flash', 'Gemini 3.7 Flash  · Vertex AI', MULTIMODAL, 1_048_576),
    ('vertex:gemini/gemini-3.6-flash', 'Gemini 3.6 Flash · Vertex AI', MULTIMODAL, 1_048_576),
    ('vertex:claude/claude-sonnet-4-6', 'Claude Sonnet 4.6 · Vertex AI', VISION, 200_000),
    ('vertex:claude/claude-haiku-4-5', 'Claude Haiku 4.5 · Vertex AI', VISION, 200_000),
    ('vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas', 'Llama 4 Maverick · Vertex AI', TEXT, 1_000_000),
    ('vertex:openweight/qwen/qwen3-coder-480b-a35b-instruct-maas', 'Qwen3 Coder · Vertex AI', TEXT, 256_000),
)

MATRIX: dict[str, ModelEntry] = {
    model_id: ModelEntry(label, caps, window) for model_id, label, caps, window in _ROWS
}
