"""`run_shell` writes for the shell the command is written in.

What has to hold:

  - the finder never mistakes the WSL launcher (`System32\\bash.exe`) for a shell, finds Git Bash next
    to Git, and keeps Windows PowerShell 5.1 the Windows default while PowerShell 7 and cmd are kinds
    of their own;
  - a machine with one shell shows the model no `shell` argument; a machine with several shows
    exactly the kinds it has, and says which dialect each is;
  - a command runs in the shell it names, the working directory survives a change of shell, and the
    exit-status protocol (pipelines, an earlier failing program) reads the same in either;
  - a command refused in the wrong dialect says how to fix it, and names the other shell only when
    the machine has one.
"""

from __future__ import annotations

import sys

import pytest

from harness.tools import shell_kinds
from harness.tools.shell import Executor, LocalExecutor, run_shell_schema, shell_tools
from harness.tools.shell_note import shell_note as _shell_note
from harness.tools.shell_kinds import BASH, CMD, POWERSHELL, PWSH, Shell, alternative, find_shells, normalise, pick_default
from harness.tools.shell_status import _parse_error_hint, _silent_exit_hint, _unknown_tool_hint

PS51 = Shell(POWERSHELL, r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "PowerShell 5.1")
PS7 = Shell(PWSH, r"C:\Program Files\PowerShell\7\pwsh.exe", "PowerShell 7")
CMDSHELL = Shell(CMD, r"C:\Windows\System32\cmd.exe", "cmd")
GITBASH = Shell(BASH, r"C:\Program Files\Git\bin\bash.exe", "Git Bash")
BASH_POSIX = Shell(BASH, "/bin/bash", "bash")

# -- the finder ---------------------------------------------------------------------------------


def _machine(paths, files=()):
    """`which` and `isfile` for an invented machine: `paths` maps a command name to where it is."""
    present = {p.lower() for p in files} | {p.lower() for p in paths.values()}
    return {
        "which": lambda name: paths.get(name),
        "exists": lambda path: path.lower() in present,
    }


def test_the_wsl_launcher_is_never_taken_for_git_bash():
    """`bash` on a Windows PATH is `C:\\Windows\\System32\\bash.exe`: a Linux filesystem in which
    `D:\\project` does not exist. A harness that asked `which("bash")` would run every command there."""
    asked = []
    machine = _machine({"bash": r"C:\Windows\System32\bash.exe", "powershell.exe": PS51.path})
    inner = machine["which"]
    machine["which"] = lambda name: (asked.append(name), inner(name))[1]
    shells = find_shells(windows=True, env={}, **machine)
    assert BASH not in shells
    assert "bash" not in asked  # Git Bash is looked for beside Git, never by a name WSL also answers to
    assert shells[POWERSHELL].label == "PowerShell 5.1"


def test_git_bash_is_found_next_to_git():
    git = r"C:\Tools\Git\cmd\git.exe"
    bash = r"C:\Tools\Git\bin\bash.exe"
    machine = _machine({"git": git, "powershell.exe": PS51.path}, files=[bash])
    shells = find_shells(windows=True, env={}, **machine)
    assert shells[BASH].path.lower().endswith(r"git\bin\bash.exe")
    assert shells[BASH].label == "Git Bash"


def test_git_bash_is_found_in_the_standard_install_folder_without_git_on_path():
    bash = r"C:\Program Files\Git\bin\bash.exe"
    machine = _machine({"powershell.exe": PS51.path}, files=[bash])
    shells = find_shells(windows=True, env={"ProgramFiles": r"C:\Program Files"}, **machine)
    assert shells[BASH].path.lower() == bash.lower()


def test_an_owner_can_name_the_bash_to_use_but_not_the_wsl_one():
    mine = r"D:\msys64\usr\bin\bash.exe"
    machine = _machine({"powershell.exe": PS51.path}, files=[mine, r"C:\Windows\System32\bash.exe"])
    assert find_shells(windows=True, env={"REEVE_BASH": mine}, **machine)[BASH].path == mine
    wsl = r"C:\Windows\System32\bash.exe"
    assert BASH not in find_shells(windows=True, env={"REEVE_BASH": wsl}, **machine)


def test_powershell_5_and_7_are_two_kinds_a_model_can_each_ask_for():
    """They are not interchangeable: 5.1 has no `&&` and starts in half the time, 7 is silent on an
    unclosed line. Asking for `pwsh` and silently getting 5.1 would be a promise the machine broke."""
    machine = _machine({"pwsh": PS7.path, "powershell.exe": PS51.path})
    shells = find_shells(windows=True, env={}, **machine)
    assert shells[POWERSHELL].label == "PowerShell 5.1" and not shells[POWERSHELL].is_modern_powershell
    assert shells[PWSH].label == "PowerShell 7" and shells[PWSH].is_modern_powershell
    assert pick_default(shells, windows=True, env={}) == POWERSHELL  # 7 is never chosen by surprise
    assert pick_default(shells, windows=True, env={"REEVE_DEFAULT_SHELL": "pwsh"}) == PWSH


def test_powershell_7_is_the_default_when_there_is_no_51():
    machine = _machine({"pwsh": PS7.path})
    shells = find_shells(windows=True, env={}, **machine)
    assert POWERSHELL not in shells and PWSH in shells
    assert pick_default(shells, windows=True, env={}) == PWSH


def test_cmd_is_found_from_comspec_on_windows_and_nowhere_else():
    machine = _machine({"powershell.exe": PS51.path}, files=[CMDSHELL.path])
    shells = find_shells(windows=True, env={"ComSpec": CMDSHELL.path}, **machine)
    assert shells[CMD].path == CMDSHELL.path and shells[CMD].is_cmd
    assert CMD not in find_shells(windows=False, env={}, **_machine({}, files=["/bin/bash"]))


def test_posix_has_bash_and_only_has_powershell_when_pwsh_is_installed():
    machine = _machine({}, files=["/bin/bash"])
    assert find_shells(windows=False, env={}, **machine) == {BASH: BASH_POSIX}
    machine = _machine({"pwsh": "/usr/bin/pwsh"}, files=["/bin/bash"])
    assert list(find_shells(windows=False, env={}, **machine)) == [PWSH, BASH]


def test_the_default_may_be_changed_only_to_a_shell_the_machine_has():
    shells = {POWERSHELL: PS51, BASH: GITBASH}
    assert pick_default(shells, windows=True, env={"REEVE_DEFAULT_SHELL": "bash"}) == BASH
    assert pick_default(shells, windows=True, env={"REEVE_DEFAULT_SHELL": "cmd"}) == POWERSHELL
    assert pick_default({BASH: BASH_POSIX}, windows=True, env={}) == BASH  # the OS's own is missing
    assert pick_default({}, windows=False, env={}) == BASH


def test_the_settings_shell_is_the_default_where_the_machine_has_it_and_the_env_still_wins():
    from harness.tools.shell_kinds import set_preferred

    shells = {POWERSHELL: PS51, BASH: GITBASH}
    assert set_preferred("git-bash") == BASH
    assert pick_default(shells, windows=True, env={}) == BASH
    assert pick_default(shells, windows=True, env={"REEVE_DEFAULT_SHELL": "powershell"}) == POWERSHELL
    assert pick_default({POWERSHELL: PS51}, windows=True, env={}) == POWERSHELL  # no Git here
    # cmd runs one line: a command may ask for it, the person may not make it everyone's default.
    assert set_preferred("cmd") == ""
    assert pick_default(shells, windows=True, env={}) == POWERSHELL


def test_a_world_keeps_the_default_it_was_built_with(tmp_path):
    """`run_shell`'s schema names the default and is part of the cached prefix: a Settings change
    mid-session must not run a command in a dialect the schema did not promise."""
    from harness.tools.shell import LocalExecutor, shell_tools
    from harness.tools.shell_kinds import set_preferred

    set_preferred("bash")
    world = LocalExecutor(cwd=tmp_path, shells={POWERSHELL: PS51, BASH: GITBASH})
    set_preferred("powershell")
    assert world.default_shell() == BASH and world._shell_for(None) == GITBASH
    schema = next(t for t in shell_tools(world) if t.__name__ == "run_shell").__tool_schema__
    assert "Git Bash, the default" in schema["function"]["description"]
    world.close()


def test_a_model_may_spell_a_kind_its_own_way_but_pwsh_is_not_powershell():
    assert [normalise(x) for x in ("PS", " sh ", "Git-Bash", "cmd.exe", "pwsh.exe", "powershell.exe")] == [
        POWERSHELL, BASH, BASH, CMD, PWSH, POWERSHELL,
    ]
    assert normalise("pwsh") == PWSH and normalise("powershell") == POWERSHELL
    assert normalise(None) == "" and normalise("") == ""


def test_a_refusal_points_at_the_shell_most_likely_to_fix_it():
    everything = {POWERSHELL: PS51, PWSH: PS7, BASH: GITBASH, CMD: CMDSHELL}
    assert alternative(PS51, everything) == BASH  # bash syntax in PowerShell is the common case
    assert alternative(PS51, {POWERSHELL: PS51, PWSH: PS7}) == PWSH  # 7 has `&&`
    assert alternative(PS7, {PWSH: PS7, CMD: CMDSHELL}) is None  # cmd is not a way out of a parse error
    assert alternative(GITBASH, everything) == POWERSHELL
    assert alternative(CMDSHELL, everything) == BASH
    assert alternative(BASH_POSIX, {BASH: BASH_POSIX}) is None


def test_the_default_dialect_is_the_operating_systems_own():
    assert shell_kinds.default_kind(windows=True) == POWERSHELL
    assert shell_kinds.default_kind(windows=False) == BASH


# -- what the model is told ---------------------------------------------------------------------


def test_one_shell_costs_no_argument_and_no_new_words():
    schema = run_shell_schema([PS51], POWERSHELL)
    assert "shell" not in schema["function"]["parameters"]["properties"]
    assert "PowerShell 5.1" in schema["function"]["description"]
    bare = run_shell_schema([], POWERSHELL)  # an executor that does not say: remote, a test double
    assert "shell" not in bare["function"]["parameters"]["properties"]


def test_two_shells_add_exactly_the_kinds_that_exist():
    schema = run_shell_schema([PS51, GITBASH], POWERSHELL)
    argument = schema["function"]["parameters"]["properties"]["shell"]
    assert argument["enum"] == [POWERSHELL, BASH]
    text = schema["function"]["description"]
    assert "Git Bash" in text and "PowerShell 5.1" in text
    assert "default" in text and "`&&`" in text
    assert "has no `&&`" in text  # 5.1's one quirk is still said, once


def test_all_four_kinds_are_offered_and_each_says_what_it_is_for():
    everything = [PS51, PS7, GITBASH, CMDSHELL]
    schema = run_shell_schema(everything, POWERSHELL)
    assert schema["function"]["parameters"]["properties"]["shell"]["enum"] == [POWERSHELL, PWSH, BASH, CMD]
    text = schema["function"]["description"]
    assert "`pwsh` (PowerShell 7: `&&` works" in text
    assert "`cmd` (cmd: batch files and legacy tools; one line, variables read as %NAME%)" in text
    assert "`powershell` (PowerShell 5.1, the default: has no `&&`" in text


def test_modern_powershell_is_not_told_it_lacks_and_and():
    text = _shell_note([PS7, BASH_POSIX], BASH)
    assert "PowerShell 7" in text and "has no `&&`" not in text
    assert "`bash` (bash, the default:" in text
    alone = _shell_note([PS7], PWSH)  # only 7 on the machine: no choice, said in one sentence
    assert "PowerShell 7" in alone and "Shells available" not in alone


def test_a_schema_is_a_copy_so_one_world_cannot_change_anothers():
    two = run_shell_schema([PS51, GITBASH], POWERSHELL)
    again = run_shell_schema([PS51], POWERSHELL)
    assert "shell" in two["function"]["parameters"]["properties"]
    assert "shell" not in again["function"]["parameters"]["properties"]


# -- the tool hands the choice to a world that offers one --------------------------------------


class _Recorder(Executor):
    def __init__(self, shells):
        self._shells = shells
        self.calls = []

    def shells(self):
        return list(self._shells)

    def run(self, command, timeout=None, **kwargs):
        self.calls.append(("run", command, kwargs))
        return {"command": command, "exit_code": 0, "output": "", "cwd": ".", "timed_out": False, "truncated": False}

    def run_background(self, command, **kwargs):
        self.calls.append(("bg", command, kwargs))
        return {"task_id": "bg-1", "status": "running"}


def _run_shell(executor):
    return next(t for t in shell_tools(executor) if t.__name__ == "run_shell")


def test_the_choice_reaches_a_world_that_offers_one():
    world = _Recorder([PS51, GITBASH])
    tool = _run_shell(world)
    tool(command="git log", shell="bash")
    tool(command="Get-Process", run_in_background=True, shell="powershell")
    tool(command="dir")
    assert world.calls == [
        ("run", "git log", {"shell": "bash"}),
        ("bg", "Get-Process", {"shell": "powershell"}),
        ("run", "dir", {}),
    ]
    assert "shell" in _run_shell(world).__tool_schema__["function"]["parameters"]["properties"]


def test_a_world_with_one_shell_never_receives_the_argument():
    """A remote POSIX host has no `shell` parameter; a model that passes one anyway is not an error."""

    class OneShell(_Recorder):
        def run(self, command, timeout=None):  # no **kwargs: it would raise if given a shell
            return super().run(command, timeout)

    world = OneShell([])
    _run_shell(world)(command="ls", shell="bash")
    assert world.calls == [("run", "ls", {})]


# -- running in a shell -------------------------------------------------------------------------


def _local(shells):
    return LocalExecutor(cwd=".", shells=shells)


def test_an_unavailable_shell_is_an_error_that_lists_what_exists():
    result = _local({POWERSHELL: PS51}).run("ls", shell="bash")
    assert result["exit_code"] is None
    assert "not available" in result["error"] and "available: powershell" in result["error"]
    background = _local({POWERSHELL: PS51}).run_background("ls", shell="bash")
    assert "not available" in background["error"]


def test_a_shell_may_be_named_the_way_models_name_it():
    executor = _local({POWERSHELL: PS51, BASH: GITBASH})
    assert executor._shell_for("sh").kind == BASH
    with pytest.raises(ValueError, match="'pwsh' is not available"):
        executor._shell_for("pwsh")  # asked for 7 on a machine without it: an error, not 5.1
    assert executor._shell_for(" BASH ").kind == BASH
    assert executor._shell_for(None).kind == (POWERSHELL if sys.platform == "win32" else BASH)


def test_git_bash_reports_its_directory_the_way_windows_spells_it():
    executor = _local({POWERSHELL: PS51, BASH: GITBASH})
    executor._is_windows = True
    assert 'pwd -W' in executor._trailer(GITBASH)
    executor._is_windows = False
    assert '"$PWD"' in executor._trailer(BASH_POSIX) and "pwd -W" not in executor._trailer(BASH_POSIX)


_REAL = shell_kinds.find_shells()
_TWO = POWERSHELL in _REAL and BASH in _REAL


@pytest.mark.skipif(BASH not in _REAL, reason="needs a real bash")
def test_a_command_runs_in_bash_and_the_exit_protocol_reads_it(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    ok = executor.run("echo hello && echo world", shell="bash")
    assert ok["exit_code"] == 0 and ok["output"].split() == ["hello", "world"]
    assert executor.run("exit 3", shell="bash")["exit_code"] == 3
    piped = executor.run("false | cat", shell="bash")
    assert piped["exit_code"] == 1 and "stage 1 of 2" in piped["exit_note"]
    masked = executor.run("ls /no/such/dir_zz; echo done", shell="bash")
    assert masked["exit_code"] != 0 and "earlier command in this line" in masked["exit_note"]


@pytest.mark.skipif(not _REAL, reason="needs a real shell")
def test_a_result_and_its_card_name_the_shell_the_command_ran_in(tmp_path):
    """A call that names no shell runs in the session's default, which is a Setting the GUI cannot
    see: a bash session's rows were drawn as PowerShell over bash's own error (2026-10-15)."""
    from harness.interface.server.app.ready import _default_shell
    from harness.tools.shell_present import RUN_SHELL_CONTRACT

    executor = LocalExecutor(cwd=tmp_path)
    default = executor.default_shell()
    result = executor.run("echo hi")
    assert result["shell"] == default
    card = RUN_SHELL_CONTRACT.present_result({"command": "echo hi"}, result).card
    assert card["card"] == "terminal" and card["shell"] == default
    # The model reads the output and its status, never the kind.
    assert default not in RUN_SHELL_CONTRACT.render({"command": "echo hi"}, result).split("[")[-1]

    class World:
        is_remote = False

        def __init__(self, executor):
            self.executor = executor

    assert _default_shell(World(executor)) == default
    remote = World(executor)
    remote.is_remote = True
    assert _default_shell(remote) is None


@pytest.mark.skipif(BASH not in _REAL, reason="needs a real bash")
def test_bash_quoting_heredocs_and_unicode_survive_the_trip(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    quoted = executor.run('echo "a \\"b\\" \'c\'"', shell="bash")
    assert quoted["output"].strip() == "a \"b\" 'c'"
    heredoc = executor.run("cat <<'EOF'\nline $one\nEOF", shell="bash")
    assert heredoc["output"].strip() == "line $one"
    assert executor.run('echo "Nguyễn Văn Ánh đạt"', shell="bash")["output"].strip() == "Nguyễn Văn Ánh đạt"


@pytest.mark.skipif(not _TWO, reason="needs PowerShell and bash")
def test_the_working_directory_survives_a_change_of_shell(tmp_path):
    (tmp_path / "sub dir").mkdir()
    executor = LocalExecutor(cwd=tmp_path)
    executor.run('cd "sub dir"', shell="bash")
    assert executor.cwd.endswith("sub dir") and "\\" in executor.cwd.replace("/", "\\")
    seen = executor.run("(Get-Location).Path", shell="powershell")["output"].strip()
    assert seen.replace("\\", "/").endswith("sub dir")
    again = executor.run("pwd", shell="bash")
    assert again["exit_code"] == 0 and again["output"].strip().endswith("sub dir")


@pytest.mark.skipif(BASH not in _REAL, reason="needs a real bash")
def test_the_managed_variables_are_exported_in_the_dialect_of_the_command(tmp_path):
    from harness.tools.shell_env import EnvContributor, ShellEnv

    env = ShellEnv()
    env.register(EnvContributor("t", {"REEVE_X": "x"}, lambda: {"REEVE_X": "it's here"}))
    executor = LocalExecutor(cwd=tmp_path, shell_env=env)
    assert executor.run('echo "$REEVE_X"', shell="bash")["output"].strip() == "it's here"


@pytest.mark.skipif(BASH not in _REAL, reason="needs a real bash")
def test_a_long_command_that_outruns_its_wait_becomes_a_task_in_bash(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    result = executor.run("sleep 30", timeout=1, shell="bash")
    assert result["status"] == "background" and result["task_id"]
    stopped = executor.background_kill(result["task_id"])
    assert stopped["status"] == "killed"


# -- a command refused in the wrong dialect -----------------------------------------------------


def test_bash_that_cannot_parse_says_so_and_names_powershell_only_when_it_exists():
    output = "/usr/bin/bash: -c: line 2: syntax error near unexpected token `then'"
    with_other = _parse_error_hint(output, GITBASH, POWERSHELL)
    assert "bash could not parse" in with_other and 'shell="powershell"' in with_other
    alone = _parse_error_hint(output, BASH_POSIX, None)
    assert "bash could not parse" in alone and "shell=" not in alone
    assert _parse_error_hint("all fine\n", GITBASH, POWERSHELL) is None


def test_powershell_that_cannot_read_a_heredoc_says_what_to_do_instead():
    output = "ParserError: The '<' operator is reserved for future use. (line 1, column 5)"
    hint = _parse_error_hint(output, PS51, BASH)
    assert "heredoc" in hint and '@\'' in hint and 'shell="bash"' in hint
    assert 'shell="bash"' not in _parse_error_hint(output, PS51, None)


def test_the_and_and_hint_offers_bash_and_keeps_the_semicolon_advice():
    output = "The token '&&' is not a valid statement separator in this version."
    hint = _parse_error_hint(output, PS51, BASH)
    assert "sequence commands with `;`" in hint and 'shell="bash"' in hint
    # Unchanged for a caller that does not say which shell it ran (the single-shell days).
    assert "sequence commands with `;`" in _parse_error_hint(output)


def test_a_posix_tool_in_powershell_is_named_only_when_bash_is_there_to_run_it():
    five = "The term 'grep' is not recognized as the name of a cmdlet, function, script file"
    seven = "The term 'sed' is not recognized as a name of a cmdlet, function, script file"
    assert 'shell="bash"' in _unknown_tool_hint(five, PS51, BASH)
    assert 'shell="bash"' in _unknown_tool_hint(seven, PS7, BASH)
    assert _unknown_tool_hint(five, PS51, None) is None
    typo = "The term 'Get-Procces' is not recognized as the name of a cmdlet, function"
    assert _unknown_tool_hint(typo, PS51, BASH) is None  # a typo'd cmdlet gets no advice it cannot use
    assert _unknown_tool_hint(five, GITBASH, POWERSHELL) is None


def test_silence_from_powershell_is_blamed_on_the_line_only_when_nothing_was_said():
    assert "unclosed" in _silent_exit_hint("", PS7)
    assert _silent_exit_hint("something printed\n", PS7) is None
    assert _silent_exit_hint("", GITBASH) is None


# -- a plugin's shell hook uses the same finder -------------------------------------------------


def test_a_plugin_hook_asks_the_finder_for_bash_not_the_path(monkeypatch):
    """`shutil.which("bash")` answers the WSL launcher on most Windows machines; the hook's paths
    do not exist in that filesystem."""
    from harness.extensions.plugins import executable

    monkeypatch.setattr(shell_kinds, "local_shells", lambda: {BASH: Shell(BASH, "/the/found/bash", "bash")})
    assert executable.shell_argv("echo hi", "bash") == ["/the/found/bash", "-c", "echo hi"]
    monkeypatch.setattr(shell_kinds, "local_shells", lambda: {})
    monkeypatch.setattr(executable.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe")
    assert executable.shell_argv("echo hi", "bash") is None
