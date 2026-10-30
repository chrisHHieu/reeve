"""The turn engine, composed from one mixin per responsibility (the sibling modules)."""

from __future__ import annotations

from .approvals import (
    USER_DENY_MESSAGE,
    USER_STOP_MESSAGE,
    ApprovalOutcome,
    Approver,
    PermissionRequest,
)
from .audit_trail import AuditTrailMixin
from .compaction import CompactionMixin
from .completion import CompletionMixin
from .dispatch import DispatchMixin
from .design_checks import DesignCheckMixin
from .context_fold import ContextFoldMixin
from .context_ledger import ContextLedgerMixin
from .context_price import ContextPriceMixin
from .controls import ControlsMixin
from .core import CoreMixin
from .finish_gates import FinishGatesMixin
from .hook_calls import HookCallsMixin
from .model_requests import ModelRequestMixin
from .limits import STEP_OUTPUT_BUDGET, STREAM_RETRIES, TRUNCATION_CONTINUES
from .main_loop import MainLoopMixin
from .model_switching import ModelSwitchMixin
from .notices import NoticesMixin
from .nudges import TODO_NUDGE_AFTER
from .outbound import OutboundMixin
from .permission_flow import PermissionFlowMixin
from .program_calls import ProgramCallsMixin
from .proposals import ProposalsMixin
from .repair_recall import RepairMixin
from .result_bounds import ResultBoundsMixin
from .result_recording import ResultRecordingMixin
from .stage_cut import StageCutMixin
from .step_recovery import StepRecoveryMixin
from .tool_batches import ToolBatchMixin
from .turn_entry import TurnEntryMixin
from .turn_limits import TurnLimitsMixin
from .turn_stamps import TurnStampsMixin
from .view_cut import ViewCutMixin


class TurnEngine(
    CoreMixin,
    ControlsMixin,
    TurnEntryMixin,
    MainLoopMixin,
    ModelSwitchMixin,
    NoticesMixin,
    StepRecoveryMixin,
    CompletionMixin,
    CompactionMixin,
    StageCutMixin,
    ContextPriceMixin,
    ContextFoldMixin,
    ContextLedgerMixin,
    ViewCutMixin,
    RepairMixin,
    HookCallsMixin,
    ModelRequestMixin,
    ToolBatchMixin,
    ResultBoundsMixin,
    FinishGatesMixin,
    TurnLimitsMixin,
    PermissionFlowMixin,
    DispatchMixin,
    DesignCheckMixin,
    ResultRecordingMixin,
    ProgramCallsMixin,
    AuditTrailMixin,
    ProposalsMixin,
    TurnStampsMixin,
    OutboundMixin,
):
    pass


__all__ = [
    "ApprovalOutcome", "Approver", "PermissionRequest", "STEP_OUTPUT_BUDGET", "STREAM_RETRIES",
    "TODO_NUDGE_AFTER", "TRUNCATION_CONTINUES", "TurnEngine", "USER_DENY_MESSAGE", "USER_STOP_MESSAGE",
]
