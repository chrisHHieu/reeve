"""SessionManager, composed from per-responsibility mixins.

The manager was one 7.8k-line module; it is now one mixin per responsibility (see the
sibling modules) sharing `deps.py`. `SessionManager` keeps the exact same public API,
so every importer is unchanged.
"""
from __future__ import annotations


from .core import CoreMixin
from .connections import ConnectionsMixin
from .inbox_handlers import InboxHandlersMixin
from .mcp import MCPMixin
from .connectors import ConnectorsMixin
from .browser_preview import BrowserPreviewMixin
from .session_folders import SessionFoldersMixin
from .artifacts import ArtifactsMixin
from .file_search import FileSearchMixin
from .file_entries import FileEntriesMixin
from .preview_grants import PreviewGrantsMixin
from .design_versions import DesignVersionsMixin
from .design_export import DesignExportMixin
from .design_lock import DesignLockMixin
from .design_review import DesignReviewMixin
from .undo import UndoMixin
from .providers import ProvidersMixin
from .codex_account import CodexAccountMixin
from .claude_subscription_account import AnthropicSubscriptionAccountMixin
from .prefs import PrefsMixin
from .model_catalog import ModelCatalogMixin
from .folder_picker import FolderPickerMixin
from .reviews import ReviewsMixin
from .source_control import SourceControlMixin
from .change_scopes import ChangeScopesMixin
from .review_assist import ReviewAssistMixin
from .branches import BranchesMixin
from .pull_requests import PullRequestsMixin
from .reveal import RevealMixin
from .allow_lists import AllowListsMixin
from .gateway_control import GatewayControlMixin
from .whatsapp_link import WhatsAppLinkMixin
from .live_clients import LiveClientsMixin
from .shutdown import ShutdownMixin
from .automation import AutomationMixin
from .approvals import ApprovalsMixin
from .goal_controls import GoalControlsMixin
from .goal_driver import GoalDriverMixin
from .wake import WakeMixin
from .turn_driver import TurnDriverMixin
from .turn_state import TurnStateMixin
from .continuity import ContinuityMixin
from .scheduled_runs import ScheduledRunsMixin
from .channels import ChannelsMixin
from .mentions import MentionsMixin
from .automation_api import AutomationApiMixin
from .session_persist import SessionPersistMixin
from .metrics_api import MetricsApiMixin
from .titles import AutoTitleMixin
from .off_turn import OffTurnMixin
from .session_roots import SessionRootsMixin
from .session_jobs import SessionJobsMixin
from .jobs_roster import JobsRosterMixin
from .workflow_runs import WorkflowRunsMixin
from .child_worktrees import ChildWorktreesMixin
from .web_search_settings import WebSearchSettingsMixin
from .session_list import SessionListMixin
from .subagent_catalog import SubagentCatalogMixin
from .external_agents import ExternalAgentsMixin
from .skills import SkillsMixin
from .projects import ProjectsMixin
from .memory_files import MemoryFilesMixin
from .permission_broker import PermissionBrokerMixin
from .revert import RevertMixin
from .user_terminal import UserTerminalMixin
from .shares import ShareMixin
from .plugins import PluginsMixin

__all__ = ["SessionManager", "last_assistant_text"]


class SessionManager(CoreMixin, ConnectionsMixin, InboxHandlersMixin, MCPMixin, ConnectorsMixin, BrowserPreviewMixin, SessionFoldersMixin, ArtifactsMixin, FileSearchMixin, FileEntriesMixin, PreviewGrantsMixin, DesignVersionsMixin, DesignExportMixin, DesignLockMixin, DesignReviewMixin, UndoMixin, ProvidersMixin, CodexAccountMixin, AnthropicSubscriptionAccountMixin, PrefsMixin, ModelCatalogMixin, FolderPickerMixin, ReviewsMixin, SourceControlMixin, ChangeScopesMixin, ReviewAssistMixin, BranchesMixin, PullRequestsMixin, AllowListsMixin, GatewayControlMixin, WhatsAppLinkMixin, LiveClientsMixin, ShutdownMixin, AutomationMixin, ApprovalsMixin, WakeMixin, TurnDriverMixin, TurnStateMixin, ContinuityMixin, ScheduledRunsMixin, GoalDriverMixin, GoalControlsMixin, ChannelsMixin, MentionsMixin, AutomationApiMixin, SessionPersistMixin, MetricsApiMixin, AutoTitleMixin, OffTurnMixin, SessionRootsMixin, SessionJobsMixin, JobsRosterMixin, WorkflowRunsMixin, ChildWorktreesMixin, WebSearchSettingsMixin, SessionListMixin, SubagentCatalogMixin, ExternalAgentsMixin, SkillsMixin, ProjectsMixin, MemoryFilesMixin, PermissionBrokerMixin, RevertMixin, RevealMixin, UserTerminalMixin, ShareMixin, PluginsMixin):
    pass
