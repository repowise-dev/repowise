"""Cross-platform process liveness and identity helpers (stdlib only).

Used by two consumers:

* The update lock (``repowise.cli.helpers``) — probe whether the PID that
  wrote ``.repowise/.update.lock`` is still alive, so a crashed update does
  not block further updates for the full wall-clock staleness window.
* The MCP stdio watchdog (``repowise.server.mcp_server``) — walk the
  ancestor chain at startup and exit when the MCP client dies, so crashed
  or force-quit agent sessions don't leak ``repowise mcp`` servers (which
  hold wiki.db handles and contend with later updates).

Design constraints, in order:

* **Never signal on Windows.** ``os.kill(pid, sig)`` on Windows is not a
  probe — any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT calls
  ``TerminateProcess``. All Windows paths go through ``ctypes``/kernel32.
* **No new dependencies** (no psutil). Windows uses kernel32 via ctypes,
  Linux uses ``/proc``, macOS/other POSIX fall back to one-shot ``ps``
  calls (startup / rare paths only, never on a per-tool-call hot path).
* **Best-effort, fail open.** Every helper returns ``None`` for "unknown"
  rather than raising; callers must treat unknown conservatively (e.g. a
  lock whose owner can't be probed is assumed alive).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Windows (ctypes / kernel32)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _ERROR_INVALID_PARAMETER = 87
    _ERROR_ACCESS_DENIED = 5
    _WAIT_TIMEOUT = 0x102
    _WAIT_OBJECT_0 = 0x0
    _STILL_ACTIVE = 259
    _TH32CS_SNAPPROCESS = 0x2
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    # Explicit signatures — the ctypes default return type is c_int, which
    # truncates 64-bit HANDLEs.
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    def _win_open_process(pid: int, access: int = _PROCESS_QUERY_LIMITED_INFORMATION) -> int | None:
        """OpenProcess with the given rights; None when it can't be opened."""
        handle = _kernel32.OpenProcess(access, False, pid)
        return handle or None

    def _win_pid_alive(pid: int) -> bool | None:
        # Preferred probe: a zero-timeout wait on the process handle.
        # WaitForSingleObject requires SYNCHRONIZE access on the handle.
        handle = _win_open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE)
        if handle:
            try:
                res = _kernel32.WaitForSingleObject(handle, 0)
                if res == _WAIT_TIMEOUT:
                    return True
                if res == _WAIT_OBJECT_0:
                    return False
                return None
            finally:
                _kernel32.CloseHandle(handle)

        err = ctypes.get_last_error()
        if err == _ERROR_INVALID_PARAMETER:
            # No such process.
            return False
        if err != _ERROR_ACCESS_DENIED:
            return None

        # SYNCHRONIZE denied — retry with query-limited rights only and use
        # the exit code (STILL_ACTIVE ⇒ alive; a real exit code of 259 is a
        # documented theoretical ambiguity we accept).
        handle = _win_open_process(pid)
        if not handle:
            # Exists (ACCESS_DENIED above proved that) but unreadable.
            return True
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)

    def _win_create_token(pid: int) -> str | None:
        handle = _win_open_process(pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            ok = _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            if not ok:
                return None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return str(value)
        finally:
            _kernel32.CloseHandle(handle)

    def _win_process_table() -> dict[int, tuple[int, str]] | None:
        """Snapshot of all processes: pid -> (ppid, exe_name)."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snapshot == _INVALID_HANDLE_VALUE:
            return None
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            table: dict[int, tuple[int, str]] = {}
            if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            while True:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    entry.szExeFile,
                )
                if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
            return table
        finally:
            _kernel32.CloseHandle(snapshot)


# ---------------------------------------------------------------------------
# Linux (/proc)
# ---------------------------------------------------------------------------


def _linux_stat_fields(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat *after* the comm field.

    The comm field is ``(name)`` and may itself contain spaces and
    parentheses, so split on the *last* ``)``.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes().decode("ascii", errors="replace")
    except OSError:
        return None
    idx = raw.rfind(")")
    if idx == -1:
        return None
    return raw[idx + 1 :].split()


def _linux_create_token(pid: int) -> str | None:
    fields = _linux_stat_fields(pid)
    if fields is None:
        return None
    # stat(5): field 22 is starttime — index 19 after stripping pid + comm
    # (fields here start at field 3, "state").
    try:
        return fields[19]
    except IndexError:
        return None


def _linux_parent_pid(pid: int) -> int | None:
    fields = _linux_stat_fields(pid)
    if fields is None:
        return None
    try:
        return int(fields[1])  # field 4, "ppid"
    except (IndexError, ValueError):
        return None


def _linux_process_name(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_bytes().decode("utf-8", errors="replace").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# macOS / generic POSIX (one-shot ``ps``; startup / rare paths only)
# ---------------------------------------------------------------------------


def _ps_field(pid: int, field: str) -> str | None:
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{field}="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    return value or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool | None:
    """Whether *pid* refers to a live process.

    Returns ``True`` / ``False`` when known, ``None`` when it can't be
    determined. Never sends a signal on Windows.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if _IS_WINDOWS:
        try:
            return _win_pid_alive(pid)
        except Exception:
            return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def process_create_token(pid: int) -> str | None:
    """Opaque identity token for *pid* derived from its creation time.

    Two probes of the same live process return equal tokens; a recycled PID
    yields a different token. Only token *equality* is meaningful — the
    encoding differs per platform (FILETIME on Windows, starttime ticks on
    Linux, ``lstart`` text on macOS). ``None`` when unavailable; callers
    must skip the comparison in that case rather than treat it as reuse.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if _IS_WINDOWS:
            return _win_create_token(pid)
        if sys.platform.startswith("linux"):
            return _linux_create_token(pid)
        return _ps_field(pid, "lstart")
    except Exception:
        return None


def parent_pid(pid: int) -> int | None:
    """Parent PID of *pid*, or ``None`` when unknown."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if _IS_WINDOWS:
            table = _win_process_table()
            if table is None or pid not in table:
                return None
            return table[pid][0]
        if sys.platform.startswith("linux"):
            return _linux_parent_pid(pid)
        value = _ps_field(pid, "ppid")
        return int(value) if value else None
    except Exception:
        return None


def process_name(pid: int) -> str | None:
    """Executable / command name of *pid*, or ``None`` when unknown."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        if _IS_WINDOWS:
            table = _win_process_table()
            if table is None or pid not in table:
                return None
            return table[pid][1]
        if sys.platform.startswith("linux"):
            return _linux_process_name(pid)
        value = _ps_field(pid, "comm")
        if value:
            # ps comm may be a full path on macOS.
            return value.rsplit("/", 1)[-1]
        return None
    except Exception:
        return None


@dataclass(frozen=True)
class ProcInfo:
    """Identity snapshot of one process: PID plus name and creation token."""

    pid: int
    name: str | None
    create_token: str | None


def ancestor_chain(pid: int | None = None, max_depth: int = 12) -> list[ProcInfo]:
    """Ancestors of *pid* (default: current process), nearest first.

    Excludes *pid* itself. Stops at PID 0/1 (idle/init), on cycles, or at
    *max_depth*. On Windows the whole chain is resolved from a single
    Toolhelp32 snapshot; on Linux from ``/proc``; on macOS via ``ps``.
    Best-effort — returns whatever prefix of the chain could be resolved.
    """
    current = pid if pid is not None else os.getpid()

    table: dict[int, tuple[int, str]] | None = None
    if _IS_WINDOWS:
        try:
            table = _win_process_table()
        except Exception:
            table = None
        if table is None:
            return []

    chain: list[ProcInfo] = []
    seen = {current}
    for _ in range(max_depth):
        if table is not None:
            entry = table.get(current)
            parent = entry[0] if entry else None
        else:
            parent = parent_pid(current)
        if parent is None or parent <= 1 or parent in seen:
            break
        seen.add(parent)
        name = table.get(parent, (None, None))[1] if table is not None else process_name(parent)
        chain.append(ProcInfo(pid=parent, name=name, create_token=process_create_token(parent)))
        current = parent
    return chain
