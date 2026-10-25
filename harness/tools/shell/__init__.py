"""One command per process, behind an `Executor` boundary.

`LocalExecutor` (`local.py`) runs each `run_shell` call in its own process. The `Executor`
interface is the hedge for a future container or VM executor, so sandboxing does not touch the
engine. The tools the model sees are built in `tools.py` from the schemas in `schemas.py`.
"""

# These names were once defined in one module, and callers still import them from here.
from ..shell_present import (  # noqa: F401
    RUN_SHELL_CONTRACT,
    SHELL_TASK_KILL_CONTRACT,
    SHELL_TASK_OUTPUT_CONTRACT,
    _shell_facts,
    _shell_kill_render,
    _shell_present_call,
    _shell_present_result,
    _shell_render,
    _shell_result_card,
    _shell_status,
    _shell_task_render,
)
from ..shell_status import _parse_error_hint  # noqa: F401
from .executor import Executor
from .limits import DEFAULT_TIMEOUT as _DEFAULT_TIMEOUT
from .limits import MAX_TIMEOUT as _MAX_TIMEOUT
from .limits import clamp_timeout as _clamp_timeout
from .local import LocalExecutor
from .schemas import RUN_SHELL_SCHEMA as _RUN_SHELL_SCHEMA
from .schemas import TASK_KILL_SCHEMA as _TASK_KILL_SCHEMA
from .schemas import TASK_OUTPUT_SCHEMA as _TASK_OUTPUT_SCHEMA
from .schemas import run_shell_schema
from .tools import SHELL_TOOL_NAMES, replace_shell_tools, shell_tools

__all__ = [
    "Executor",
    "LocalExecutor",
    "RUN_SHELL_CONTRACT",
    "SHELL_TASK_KILL_CONTRACT",
    "SHELL_TASK_OUTPUT_CONTRACT",
    "SHELL_TOOL_NAMES",
    "replace_shell_tools",
    "run_shell_schema",
    "shell_tools",
]
