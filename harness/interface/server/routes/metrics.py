"""The Metrics page's read endpoint — one call returns the whole page payload.

The payload is assembled by `MetricsApiMixin.ops_metrics` from the metrics tables alone:
headline totals, daily buckets, tokens and harness numbers per model and per day, tool health,
recent tool failures, the harness fold, the ship days, the last self-review, and the ±2σ
"notable changes" strip.
"""

from __future__ import annotations

from typing import Any

from fastapi import Query


def register_metrics(app, manager):
    @app.get("/v1/metrics")
    def metrics_get(
        range_name: str = Query("7d", alias="range"),
    ) -> dict[str, Any]:
        # Range vocabulary is the UI's: today | 7d | 30d | all. Unknown values fall back
        # to 7d inside the manager, so an old client can never 500 the page.
        return manager.ops_metrics(range_name)

    @app.post("/v1/metrics/review")
    def metrics_review_refresh() -> dict[str, Any]:
        # The page's refresh of its self-review: started in a child process, answered at once; the
        # result arrives as a `metrics_changed` push when the child lands.
        return {"started": manager.refresh_self_review()}
