"""Ask the provider what its models can do, instead of maintaining the answer by hand.

The curated matrix and the name heuristics in `capabilities.py` are both guesses about
facts a provider publishes. Measured against OpenRouter's own catalogue on 2026-08-26:

  · 250 of its 417 models accept images. reeve called ALL 250 blind — including
    `google/gemini-3.7-flash` and one model with "vision" in its name.
  · Of the 8 curated matrix rows whose ids appear in that catalogue, 5 had vision wrong
    and 2 had the context window wrong — `z-ai/glm-5.2` was recorded as 128k against a
    real 1,048,576, so compaction fired eight times too early on it.

The second number is the important one: the drift is not in the fallback, it is in the
hand-maintained truth. A guess that is checked once and then copied ages badly, and
nobody notices, because being wrong this way is silent — an agent that cannot see just
looks like a model that cannot see.

Only OpenRouter publishes a catalogue without a key (verified: Together, Mistral,
Fireworks, Gemini, Z AI and DeepSeek all answer 401/403 unauthenticated). So this covers
one provider today and is shaped to take more: a fetcher per provider, one cache, one
lookup. Whether the others' catalogues carry capabilities at all is unknown until someone
holds a key, and is deliberately not guessed at here.

Three rules:

**It never blocks a session.** No catalogue, no network, a malformed body — every failure
path returns None and the caller falls through to the matrix and then the heuristics,
which is exactly today's behaviour.

**It never touches the network on the hot path.** `capabilities_for` runs on every
outbound assembly. The fetch happens at most once per process, behind a short deadline,
and the answer is kept on disk so the next process starts warm — a stale catalogue beats
a guess, so an expired cache is used rather than discarded when the refresh fails.

**The catalogue wins.** Where it and the matrix disagree, the provider's own statement of
what its model accepts is the fact, and the matrix row is a copy that has drifted.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .catalog_ids import catalogue_id

#: Refresh at most daily. Model capabilities change when a model is added or replaced, not
#: hourly, and a stale answer is still enormously better than a guess.
TTL_SECONDS = 24 * 60 * 60

#: The fetch is on the first-use path of a session build, so it gets a short leash. Falling
#: back to the cache (or to the matrix) costs correctness on unlisted models; hanging a
#: session start costs the person their time, which is worse.
FETCH_TIMEOUT = 6.0

#: Bump when the PROJECTION changes (which fields an entry keeps, or WHICH PROVIDERS are
#: mapped), not when the source changes. A cache written by an older projection is treated
#: as stale and refetched ONCE, so a new field (e.g. `reasoning_options`) — or a newly
#: mapped provider like `opencode-go` — reaches every warm install without waiting out the
#: 24h TTL; otherwise a feature would silently do nothing until the cache expired.
CACHE_VERSION = 9

_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

#: The community catalogue most of the ecosystem reads: 200+ providers,
#: every model with name, modalities, reasoning, tool calling, context and per-token
#: cost. Public, no key. Only the providers Reeve can actually route are projected —
#: a model nobody can call is not a suggestion.
_MODELS_DEV_URL = "https://models.dev/api.json"

#: models.dev provider id → reeve provider id. Deliberately a table, not a guess:
#: `google` is Reeve's `gemini`, `google-vertex` its `vertex`, reseller ids differ
#: (`togetherai`, `fireworks-ai`, `moonshotai`), and anything absent here is dropped.
_MODELS_DEV_PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
    "google-vertex": "vertex",
    "amazon-bedrock": "bedrock",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "xai": "xai",
    "mistral": "mistral",
    "togetherai": "together",
    "fireworks-ai": "fireworks",
    "moonshotai": "kimi",
    "zai": "zai",
    # Zhipu AI's subscription Coding Plan: its own models.dev provider (four GLM-5.3/4.6v
    # rows with 1M windows) and its own endpoint, so it maps to its own Reeve id.
    "zhipuai-coding-plan": "zhipuai-coding-plan",
    # The international sibling (`zai-coding-plan`): same OpenAI-compatible
    # coding plan shape, its own endpoint (api.z.ai) and its own seven GLM rows.
    "zai-coding-plan": "zai-coding-plan",
    "minimax": "minimax",
    "alibaba": "qwen",
    "volcengine": "ark",
    # OpenCode's own gateway (the provider `opencode auth login` calls `opencode-go`), so
    # the source id and Reeve's id are the same string.
    "opencode-go": "opencode-go",
}

#: Some plans serve model ids that models.dev files under a SIBLING provider — the plan's
#: own entry lists only the newest few (Zhipu's Coding Plan showed 10 models on 2026-09-14
#: and 4 on 2026-09-18, while a bundled snapshot still offers all 10). The facts are
#: the same models, same vendor, same ids, so the plan's picker row borrows them from the
#: provider that still publishes them. Keyed reeve provider → {model id: models.dev id}.
#: The plan's OWN entry always wins where both exist (`setdefault` below).
_MODELS_DEV_BORROWED_MODELS: dict[str, dict[str, str]] = {
    "zhipuai-coding-plan": {
        "glm-5.2": "zai-coding-plan",
        "glm-5.2-highspeed": "zai-coding-plan",
        "glm-5-turbo": "zai-coding-plan",
        "glm-4.7": "zai-coding-plan",
        "glm-5.1": "zhipuai",
        "glm-5v-turbo": "zhipuai",
    },
}

#: models.dev marks a model whose transport differs from its provider's default with an
#: explicit `provider.npm`. The value is kept on the projected entry as `wire`, so a client
#: that CAN speak more than one wire (OpenCode Go: see providers/opencode_go.py) routes the
#: call by the model's package instead of the model being hidden.
_WIRE_BY_NPM = {
    "@ai-sdk/openai-compatible": "chat",
    "@ai-sdk/anthropic": "messages",
    "@ai-sdk/openai": "responses",
    "@ai-sdk/google": "gemini",
}


class ModelCatalog:
    """Provider-published model facts, cached on disk and in memory.

    Keyed by the FULL routed id (`openrouter:stealth/ox-alpha`) so callers hold one
    vocabulary — the same one the matrix and the router already use.
    """

    def __init__(self, path: Path, *, ttl: float = TTL_SECONDS) -> None:
        self.path = Path(path)
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._entries: Optional[dict[str, dict[str, Any]]] = None
        self._fetched_at = 0.0
        #: One attempt per process. A provider that is down stays down for this session;
        #: retrying on every lookup would put a network call back on the hot path.
        self._tried = False

    # -- lookup -------------------------------------------------------------------

    def get(self, model: str, *, fetch: bool = True) -> Optional[dict[str, Any]]:
        """`{vision, context_length, tools, …}` for a routed model id, or None.

        None means "no opinion" — never "no". The caller must fall through, or an
        unreachable catalogue would silently disable every capability it would have
        confirmed. `fetch=False` is the disk-only read a hot path uses when a network
        download would be the wrong price for the answer.
        """
        entries = self._load(fetch=fetch)
        if not entries:
            return None
        return entries.get(str(model))

    # -- storage ------------------------------------------------------------------

    def _load(self, *, fetch: bool = True) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._entries is not None and not self._stale():
                return self._entries
            if self._entries is None:
                self._read_disk()
            if fetch and self._stale() and not self._tried:
                self._tried = True
                fetched = self._fetch()
                if fetched:
                    self._entries = fetched
                    self._fetched_at = time.time()
                    self._write_disk()
            return self._entries or {}

    def _stale(self) -> bool:
        return (time.time() - self._fetched_at) > self.ttl

    def _read_disk(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries")
            if isinstance(entries, dict):
                self._entries = entries
                # A cache from an older projection keeps its facts (a stale answer beats a
                # guess) but is marked stale, so the next `_load(fetch=True)` refetches
                # the new fields once. If that fetch fails, the old entries still serve.
                fresh = int(raw.get("version") or 0) == CACHE_VERSION
                self._fetched_at = float(raw.get("fetched_at") or 0.0) if fresh else 0.0
                return
        except Exception:
            pass
        self._entries = {}
        self._fetched_at = 0.0

    def _write_disk(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CACHE_VERSION,
                "fetched_at": self._fetched_at,
                "entries": self._entries,
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass  # a cache that cannot be written is a slower start, not a failure

    # -- fetching -----------------------------------------------------------------

    def _fetch(self) -> dict[str, dict[str, Any]]:
        """Every provider catalogue we can read, merged. Never raises."""
        out: dict[str, dict[str, Any]] = {}
        out.update(fetch_openrouter())
        return out


def fetch_openrouter() -> dict[str, dict[str, Any]]:
    """OpenRouter's public catalogue — no key, 417 models, three facts each.

    `input_modalities` is the authoritative answer to "can this model see", and
    `supported_parameters` containing `tools` is the authoritative answer to "can it call
    tools". Both were being guessed.
    """
    try:
        import httpx

        response = httpx.get(_OPENROUTER_URL, timeout=FETCH_TIMEOUT)
        if response.status_code != 200:
            return {}
        rows = response.json().get("data") or []
    except Exception:
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            architecture = row.get("architecture") or {}
            modalities = architecture.get("input_modalities") or []
            params = row.get("supported_parameters") or []
            window = row.get("context_length")
            entries[f"openrouter:{model_id}"] = {
                "vision": "image" in modalities,
                # Only the modalities this harness can act on; "file" (OpenRouter's word for
                # documents) is deliberately not read as PDF support.
                "media": [str(m) for m in modalities if m in ("audio", "video")],
                "tools": "tools" in params,
                "context_length": int(window) if isinstance(window, int) else None,
            }
        except Exception:
            continue  # one malformed row must not cost the other 416
    return entries


_catalog: Optional[ModelCatalog] = None
_catalog_lock = threading.Lock()


class ModelsDevCatalog(ModelCatalog):
    """The models.dev catalogue, with a hot/cold split on the network.

    A session build asks for a model's capabilities on every outbound assembly; it must
    never wait on a 4.5 MB download for that answer. So `get` is DISK-ONLY (a miss falls
    through to the matrix and the heuristics, exactly as before), while `entries()` —
    the picker's list path, an explicit user-facing read — is allowed to fetch, cache
    and then answer the hot path for the rest of the process.
    """

    def get(self, model: str) -> Optional[dict[str, Any]]:
        entries = self._load(fetch=False)
        if not entries:
            return None
        key = catalogue_id(str(model), entries)  # a routed Vertex/Bedrock id is filed without its routing segment
        return entries.get(key) if key else None

    def entries(self, *, fetch: bool = True) -> dict[str, dict[str, Any]]:
        """Every projected entry; `fetch=True` may refresh from the network once."""
        return self._load(fetch=fetch)

    def _fetch(self) -> dict[str, dict[str, Any]]:
        return fetch_models_dev()


def fetch_models_dev() -> dict[str, dict[str, Any]]:
    """models.dev, projected onto Reeve's provider ids. Never raises.

    One entry per model, keyed by the full routed id (`deepseek:deepseek-v4-flash`), so
    the vocabulary matches the matrix, the router and the picker. Fields missing from
    the source are left OUT rather than defaulted — "the catalogue did not say" and
    "the catalogue said no" must stay different answers, the same rule the OpenRouter
    fetcher follows.

    Two escape hatches, both env-only: `REEVE_DISABLE_MODELS_FETCH=1` for a machine
    that must never call out (offline, air-gapped, a locked-down CI), and
    `REEVE_MODELS_URL` to point at a mirror. Neither affects the disk cache, so a
    warm install keeps answering from `model-facts.json`.
    """
    if os.environ.get("REEVE_DISABLE_MODELS_FETCH"):
        return {}
    try:
        import httpx

        source = os.environ.get("REEVE_MODELS_URL") or _MODELS_DEV_URL
        response = httpx.get(source, timeout=FETCH_TIMEOUT)
        if response.status_code != 200:
            return {}
        providers = response.json()
    except Exception:
        return {}
    if not isinstance(providers, dict):
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for source_id, reeve_id in _MODELS_DEV_PROVIDERS.items():
        provider = providers.get(source_id)
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            entry = _models_dev_entry(model_id, model)
            if entry:
                entries[f"{reeve_id}:{model_id}"] = entry
    # Sibling-provider facts for plans whose own entry lists only part of their lineup
    # (see `_MODELS_DEV_BORROWED_MODELS`). Never overwrites the plan's own statement.
    for reeve_id, borrow in _MODELS_DEV_BORROWED_MODELS.items():
        for model_id, source_id in borrow.items():
            source = providers.get(source_id)
            if not isinstance(source, dict):
                continue
            model = (source.get("models") or {}).get(model_id)
            if not isinstance(model, dict):
                continue
            entry = _models_dev_entry(model_id, model)
            if entry:
                entries.setdefault(f"{reeve_id}:{model_id}", entry)

    # For anthropic-subscription, inherit all anthropic models from models.dev
    anthropic_source = providers.get("anthropic")
    if isinstance(anthropic_source, dict):
        for model_id, model in (anthropic_source.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            entry = _models_dev_entry(model_id, model)
            if entry:
                entries.setdefault(f"anthropic-subscription:{model_id}", entry)
    return entries


def _models_dev_entry(model_id: str, model: dict[str, Any]) -> dict[str, Any]:
    """One models.dev row, reduced to what Reeve reads (facts, never defaults)."""
    entry: dict[str, Any] = {"name": str(model.get("name") or model_id)}
    # The transport, when the model overrides the provider's default. Absent = the
    # provider's own default wire (for Reeve's compat providers, chat/completions).
    override = (model.get("provider") or {}).get("npm")
    if override:
        entry["wire"] = _WIRE_BY_NPM.get(str(override), "chat")
    # The model's own endpoint, when the catalogue names one. OpenCode Go gives each wire its own
    # root (`/inference/go/openai/v1`, `/anthropic/v1`, `/google/v1beta`) and moved them on
    # 2026-10-20; reading them here is how the client follows the next move without a release.
    endpoint = (model.get("provider") or {}).get("api")
    if isinstance(endpoint, str) and endpoint.startswith("https://"):
        entry["api"] = endpoint.rstrip("/")
    for source_key, target_key in (
        ("attachment", "vision"),
        ("tool_call", "tools"),
        ("reasoning", "reasoning"),
        ("temperature", "temperature"),
    ):
        if source_key in model:
            entry[target_key] = bool(model.get(source_key))
    modalities = model.get("modalities")
    if isinstance(modalities, dict):
        for direction in ("input", "output"):
            values = modalities.get(direction)
            if isinstance(values, list):
                entry[direction] = [str(v) for v in values]
    # `reasoning_options` is the provider's own statement of WHICH thinking levels it
    # takes (an effort list, a toggle, or a token budget range). It is kept verbatim and
    # reduced in `variants.py` — the same "the catalogue wins" rule as every other fact.
    # Absent means "the catalogue did not say", never "no levels".
    options = model.get("reasoning_options")
    if isinstance(options, list):
        kept = _reasoning_options(options)
        if kept:
            entry["reasoning_options"] = kept
    limit = model.get("limit")
    if isinstance(limit, dict):
        context = limit.get("context")
        output = limit.get("output")
        if isinstance(context, int) and context > 0:
            entry["context_length"] = context
        if isinstance(output, int) and output > 0:
            entry["output_length"] = output
    cost = model.get("cost")
    if isinstance(cost, dict):
        entry["cost"] = {
            key: float(cost[key])
            for key in ("input", "output", "cache_read", "cache_write")
            if isinstance(cost.get(key), (int, float))
        }
    for source_key in ("status", "release_date", "family"):
        if model.get(source_key):
            entry[source_key] = str(model[source_key])
    entry["source"] = "models.dev"
    return entry


def _reasoning_options(options: list[Any]) -> list[dict[str, Any]]:
    """The reasoning_options list, reduced to the three shapes `variants.py` reads.

    models.dev states one of: `{type:"effort", values:[...]}`, `{type:"toggle"}`, or
    `{type:"budget_tokens", min, max}`. Malformed rows are dropped rather than
    defaulted — an option nobody can interpret is not an option.
    """
    kept: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        kind = option.get("type")
        if kind == "effort":
            values = option.get("values")
            if isinstance(values, list):
                cleaned = [str(v) for v in values if isinstance(v, str) and v]
                if cleaned:
                    kept.append({"type": "effort", "values": cleaned})
        elif kind == "toggle":
            kept.append({"type": "toggle"})
        elif kind == "budget_tokens":
            entry: dict[str, Any] = {"type": "budget_tokens"}
            for bound in ("min", "max"):
                value = option.get(bound)
                if isinstance(value, (int, float)) and value > 0:
                    entry[bound] = int(value)
            kept.append(entry)
    return kept


_models_dev: Optional[ModelsDevCatalog] = None
_models_dev_lock = threading.Lock()


def models_dev() -> ModelsDevCatalog:
    """The process-wide models.dev catalogue, beside the other machine-local state."""
    global _models_dev
    with _models_dev_lock:
        if _models_dev is None:
            from harness.safety.secrets import state_dir

            _models_dev = ModelsDevCatalog(Path(state_dir()) / "model-facts.json")
        return _models_dev


def set_models_dev(replacement: Optional[ModelsDevCatalog]) -> None:
    """Swap the process-wide models.dev catalogue — tests, or a different cache path."""
    global _models_dev
    with _models_dev_lock:
        _models_dev = replacement


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Optional[float]:
    """USD for one request/run's token split, from the DISK price sheet (never network).

    Returns None when the sheet does not price the model — "unknown" must not read as
    "free" (a genuinely free model's sheet entry has zero rates and returns 0.0). Cache
    writes bill at the input rate when the sheet carries no write-side rate, which is how
    implicit caching (OpenAI, DeepSeek, most compat vendors) is billed.
    """
    try:
        entry = models_dev().get(model) or {}
    except Exception:
        return None
    cost = entry.get("cost") or {}
    if not cost:
        return None
    rate_in = float(cost.get("input") or 0.0)
    rate_out = float(cost.get("output") or 0.0)
    rate_read = float(cost.get("cache_read") or 0.0)
    rate_write = cost.get("cache_write")
    rate_write = rate_in if rate_write is None else float(rate_write or 0.0)
    return (
        input_tokens / 1e6 * rate_in
        + output_tokens / 1e6 * rate_out
        + cache_read_tokens / 1e6 * rate_read
        + cache_write_tokens / 1e6 * rate_write
    )


def facts_for(model: str) -> Optional[dict[str, Any]]:
    """Everything the catalogues know about one model, merged, or None.

    models.dev first (it carries name/reasoning/cost/modalities), the provider's own
    catalogue over it (OpenRouter states modalities and tools per exact id), then the
    caller layers the matrix on top where it needs a display label or verified caps.
    Hot-safe: models.dev is read from disk only here.
    """
    out: dict[str, Any] = {}
    published = models_dev().get(model)
    if published:
        out.update(published)
    try:
        own = catalog().get(model)
    except Exception:
        own = None
    if own:
        out.update(own)
    return out or None


def catalog():
    """The process-wide catalogue, beside the other machine-local state."""
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            from harness.safety.secrets import state_dir

            _catalog = ModelCatalog(Path(state_dir()) / "model-catalog.json")
        return _catalog


def set_catalog(replacement: Optional[ModelCatalog]) -> None:
    """Swap the process-wide catalogue — for tests, and for a caller that wants a
    different cache location. `None` restores the default on next use."""
    global _catalog
    with _catalog_lock:
        _catalog = replacement
