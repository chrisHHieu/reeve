"""The four plain-HTTP list shapes: OpenAI-compatible, Anthropic, Gemini and Ollama."""

from __future__ import annotations

from typing import Any

from .fetch import Listing, get_json, tidy


def _ids(rows: Any, field: str) -> list[str]:
    return tidy(row.get(field) for row in rows if isinstance(row, dict))


def openai_compatible(base: str, key: str, timeout: float) -> Listing:
    """`GET {base}/models`, ids under `data` (or `models`)."""
    payload, error = get_json(
        base.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {key}"} if key else {},
        timeout=timeout,
    )
    if payload is None:
        return None, error
    return _ids(payload.get("data") or payload.get("models") or [], "id"), None


def anthropic(base: str, key: str, timeout: float) -> Listing:
    """`GET {base}/v1/models`."""
    payload, error = get_json(
        base.rstrip("/") + "/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=timeout,
    )
    if payload is None:
        return None, error
    return _ids(payload.get("data") or [], "id"), None


def gemini(base: str, key: str, timeout: float) -> Listing:
    """`GET {base}/v1beta/models`, keeping the models that can chat."""
    payload, error = get_json(
        base.rstrip("/") + "/v1beta/models",
        headers={"x-goog-api-key": key} if key else {},
        params={"key": key} if key else None,
        timeout=timeout,
    )
    if payload is None:
        return None, error
    ids: list[str] = []
    for row in payload.get("models") or []:
        name = str(row.get("name") or "") if isinstance(row, dict) else ""
        if not name:
            continue
        # The list mixes embeddings and other modalities in. Keep what can chat when the provider
        # says, and keep everything when it does not.
        methods = row.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        ids.append(name.split("/")[-1])
    return tidy(ids), None


def ollama(base: str, timeout: float) -> Listing:
    """Ollama's native `/api/tags`, whatever `/v1` form the base was given in."""
    root = (base or "http://localhost:11434").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    payload, error = get_json(root + "/api/tags", headers={}, timeout=timeout)
    if payload is None:
        return None, error
    return _ids(payload.get("models") or [], "name"), None


def by_shape(name: str, root: str, key: str, timeout: float) -> Listing:
    """The fetch that fits the provider's shape; OpenAI-compatible for everything else."""
    if name == "anthropic":
        return anthropic(root, key, timeout)
    if name == "gemini":
        return gemini(root, key, timeout)
    return openai_compatible(root, key, timeout)
