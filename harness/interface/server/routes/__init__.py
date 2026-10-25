"""Route groups extracted from `create_app` (see sibling modules)."""
from .core import register_core
from .inbox import register_inbox
from .metrics import register_metrics
from .skills import register_skills
from .plugins import register_plugins
from .workspaces import register_workspaces
from .sessions import register_sessions
from .preview import register_preview
from .child_worktrees import register_child_worktrees
from .memory import register_memory
from .projects import register_projects
from .mcp import register_mcp
from .connector_toolsets import register_connector_toolsets
from .connectors import register_connectors
from .slack_user_token import register_slack_user_token
from .providers import register_providers
from .settings import register_settings
from .automations import register_automations
from .terminal import register_terminal
from .share import register_share
from .remote import register_remote

__all__ = [
    "register_core",
    "register_inbox",
    "register_metrics",
    "register_skills",
    "register_plugins",
    "register_workspaces",
    "register_sessions",
    "register_preview",
    "register_child_worktrees",
    "register_memory",
    "register_projects",
    "register_mcp",
    "register_connector_toolsets",
    "register_connectors",
    "register_slack_user_token",
    "register_providers",
    "register_settings",
    "register_automations",
    "register_terminal",
    "register_share",
    "register_remote",
]
