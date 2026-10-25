"""A Slack that runs in the test process: enough Web API and Socket Mode for the real adapter.

`server.FakeSlack` is the object; `platform/docs/FAKE-SLACK-SPEC.md` is what it promises.
"""

from __future__ import annotations

from .server import FakeSlack

__all__ = ["FakeSlack"]
