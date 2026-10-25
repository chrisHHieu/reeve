"""Does each provider route report usage the harness can trust? (live, spends a little)

The trigger signal, the token budget and every KPI read the provider's usage. A route that
reports only part of the prompt starves compaction — opencode-go's Anthropic-wire qwen
reported a flat 12k while the session grew, and no compaction could ever fire. This sends
two requests per model: a ~4k-token prompt, then the same prompt plus ~4k more, and checks

    grows    the second prompt priced about the appended amount higher
    cached   the second call read part of its prefix from the provider cache (reported,
             not required: some routes do not cache or do not say so)

    .venv\\Scripts\\python.exe scripts\\check_provider_usage.py opencode-go:qwen3.8-flash ...
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.context.compaction import estimate_tokens  # noqa: E402


FILLER = " ".join(f"line {i}: the quick brown fox jumps over the lazy dog." for i in range(350))


def check(router, model: str) -> str:
    first = [
        {"role": "system", "content": "You answer with one word."},
        {"role": "user", "content": "Context A:\n" + FILLER + "\nReply: ok"},
    ]
    second = first + [
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "Context B:\n" + FILLER.replace("fox", "cat") + "\nReply: ok"},
    ]
    try:
        a = router.complete(model=model, messages=first, max_tokens=64).usage
        b = router.complete(model=model, messages=second, max_tokens=64).usage
    except Exception as exc:  # noqa: BLE001 — a failing route is a result
        return f"{model:38s} ERROR {type(exc).__name__}: {str(exc)[:90]}"
    if a is None or b is None:
        return f"{model:38s} NO USAGE reported"
    appended = estimate_tokens(second[len(first):])
    grew = b.context_tokens - a.context_tokens
    ok = grew >= 0.5 * appended
    return (
        f"{model:38s} {'grows' if ok else 'PARTIAL'}  first={a.context_tokens:>6,} "
        f"second={b.context_tokens:>6,} (+{grew:,} for ~{appended:,} appended)  "
        f"cache_read={b.cache_read:,}"
    )


def main(models: list[str]) -> int:
    from harness.llm.providers.router import ProviderRouter
    from harness.safety.secrets import SecretStore

    router = ProviderRouter(SecretStore())
    bad = 0
    for model in models:
        line = check(router, model)
        bad += "PARTIAL" in line or "ERROR" in line or "NO USAGE" in line
        print(line, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
