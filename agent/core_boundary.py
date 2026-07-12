"""Contract C7 — the Core/Customizable boundary + hard runtime write-guard (FG-14).

The repo is split into two areas:

* **Core** — fixed system machinery (``run_agent.py``, ``model_tools.py``,
  ``toolsets.py``, ``cli.py``, ``hermes_state.py``, the core ``gateway/``, the
  FG-12 change engine, the FG-18 GTS engine, this guard, and the manifest
  itself). Enumerated by ``core_manifest.yaml`` at the repo root.
* **Customizable** — everything else (plugins, skills, user-created tools,
  behavioural ``config.yaml``, user-owned app data).

The **runtime LLM agent must never modify Core**, no matter what a user asks it
to do — this prevents a user from talking the agent into breaking the system
(decision D10). :func:`check_core_write` is the hard, fail-closed guard wired
into the agent's file-write chokepoint (``tools/file_operations.py``). It:

* resolves the write target **escape-safely** (``..``, symlinks, absolute
  paths, and mount escapes are all resolved via ``realpath`` before
  classification), so a path that *resolves* into Core is caught regardless of
  how it was spelled;
* is **fail-closed**: if ``core_manifest.yaml`` is missing or unparseable, a
  baked-in copy of the Core globs (:data:`_FALLBACK_CORE_GLOBS`) is used, so
  Core stays protected even if the manifest is deleted or corrupted;
* has **no user override** — there is no config/env/prompt switch that disables
  it, and the manifest + this module are themselves Core (self-protecting);
* on denial, emits a **C5 change-audit event** + a **C8 trace event**
  (kind ``core_denied``) via :func:`record_core_denied`.

This boundary applies to the **runtime agent only**. The human developer path
(git, PRs, ``hermes update``) does not go through the agent's file-write tools
and is therefore unaffected — humans still change Core via the repo/PR flow.

**Scope note (defense in depth, honestly framed).** The agent's raw terminal
tool runs as the same OS user and can, in principle, still shell out to
overwrite a file — exactly as the existing credential write-deny list
acknowledges. This guard is a *hard block at the structured file-write
chokepoint* (the path the agent actually uses to edit files) plus a durable
audit trail; it is the enforceable, testable boundary the runtime agent's tools
respect. Closing the raw-shell hole belongs to the terminal-backend sandbox,
not here.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Fail-closed fallback — a baked-in copy of the manifest's Core globs.
#
# Used ONLY when core_manifest.yaml cannot be read/parsed. Because this module
# is itself Core (self-protecting), the fallback cannot be tampered with by the
# runtime agent. Keep it in sync with core_manifest.yaml — the manifest is the
# source of truth; this is the safety net.
# ---------------------------------------------------------------------------
_FALLBACK_CORE_GLOBS: tuple[str, ...] = (
    "run_agent.py",
    "model_tools.py",
    "toolsets.py",
    "cli.py",
    "hermes_state.py",
    "gateway/**",
    "hermes_cli/changes.py",
    "hermes_cli/changes_cli.py",
    "hermes_cli/goal_registry.py",
    "hermes_cli/goals.py",
    "hermes_cli/task_registry.py",
    "hermes_cli/gts.py",
    "agent/core_boundary.py",
    "core_manifest.yaml",
)

_MANIFEST_BASENAME = "core_manifest.yaml"


def get_core_root() -> Path:
    """Return the resolved repo root that Core globs are relative to.

    This module lives at ``<repo_root>/agent/core_boundary.py``, so the repo
    root is this file's grandparent. Resolved (symlinks collapsed) so it can be
    compared against resolved write targets.
    """
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Manifest loading (cached, mtime-invalidated, fail-closed)
# ---------------------------------------------------------------------------

_glob_lock = threading.Lock()
# Cache: (manifest_path_str, mtime_ns) -> tuple[str, ...]. A miss (missing file,
# parse error, empty) falls back to _FALLBACK_CORE_GLOBS.
_glob_cache: dict[tuple[str, int], tuple[str, ...]] = {}


def _manifest_path() -> Path:
    return get_core_root() / _MANIFEST_BASENAME


def _parse_globs(text: str) -> tuple[str, ...]:
    """Parse ``core_globs`` out of the manifest YAML. Returns () on any problem."""
    try:
        import yaml  # local import: fail-closed if PyYAML is unavailable
    except Exception:
        return ()
    try:
        data = yaml.safe_load(text)
    except Exception:
        return ()
    if not isinstance(data, dict):
        return ()
    raw = data.get("core_globs")
    if not isinstance(raw, list):
        return ()
    globs = tuple(
        g.strip() for g in raw if isinstance(g, str) and g.strip()
    )
    return globs


def load_core_globs() -> tuple[str, ...]:
    """Return the active Core globs — from the manifest, or the fail-closed fallback.

    Cached by manifest path + mtime so a committed-manifest read is O(stat) on
    the hot path, while an edited/replaced manifest is picked up automatically.
    Any failure (missing file, unreadable, unparseable, empty ``core_globs``)
    yields :data:`_FALLBACK_CORE_GLOBS` — Core is never left unprotected.
    """
    path = _manifest_path()
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return _FALLBACK_CORE_GLOBS

    key = (str(path), mtime)
    with _glob_lock:
        cached = _glob_cache.get(key)
    if cached is not None:
        return cached

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_CORE_GLOBS

    globs = _parse_globs(text)
    if not globs:
        # Parse failure / empty manifest → fail closed, but do not poison the
        # cache with the fallback keyed on this mtime (a fix should take effect
        # without a version bump).
        return _FALLBACK_CORE_GLOBS

    with _glob_lock:
        _glob_cache[key] = globs
    return globs


# ---------------------------------------------------------------------------
# Escape-safe path classification
# ---------------------------------------------------------------------------

def _resolve(path: str | os.PathLike[str]) -> Path:
    """Resolve a path escape-safely for classification.

    ``os.path.realpath`` collapses ``..`` segments, follows symlinks, and
    normalizes absolute paths and mount points — so a target that *resolves*
    into Core is classified as Core no matter how it was spelled
    (``a/../run_agent.py``, a symlink pointing at ``run_agent.py``, an absolute
    path, or a bind-mount alias). ``expanduser`` first so ``~`` targets resolve.
    Works for not-yet-existing files: the existing prefix's symlinks resolve and
    the final component is normalized lexically.
    """
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def _glob_matches(rel_posix: str, pattern: str) -> bool:
    """True if the repo-relative POSIX path matches a Core glob.

    ``dir/**`` matches the directory itself and everything beneath it. Other
    patterns use fnmatch semantics (note fnmatch's ``*`` also spans ``/``,
    which only ever *widens* the deny set — safe for a fail-closed guard).
    """
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return rel_posix == base or rel_posix.startswith(base + "/")
    return fnmatch.fnmatch(rel_posix, pattern)


def classify_core_path(path: str | os.PathLike[str]) -> Optional[str]:
    """Return the Core glob a path matches, or ``None`` if it is Customizable.

    Pure classification (no side effects). A target outside the repo root is
    Customizable (the guard only protects the repo's Core machinery). A target
    inside the repo is Core iff it matches one of :func:`load_core_globs`.
    """
    resolved = _resolve(path)
    core_root = get_core_root()
    try:
        rel = resolved.relative_to(core_root)
    except ValueError:
        # Resolves outside the Hermes repo → not Core.
        return None
    rel_posix = rel.as_posix()
    if rel_posix == ".":
        return None
    for glob in load_core_globs():
        if _glob_matches(rel_posix, glob):
            return glob
    return None


def is_core_path(path: str | os.PathLike[str]) -> bool:
    """True if writing ``path`` would modify a Core file (see :func:`classify_core_path`)."""
    return classify_core_path(path) is not None


# ---------------------------------------------------------------------------
# C5 change-audit + C8 trace emission (pluggable, degrade-gracefully)
# ---------------------------------------------------------------------------

# Optional sinks, registered by richer subsystems when present:
#   * a C5 change recorder (FG-12 ChangeLog) — records the denial as a
#     change-audit row in the append-only log; and
#   * a C8 trace emitter (FG-16 interaction trace) — records a `core_denied`
#     trace row joined to the originating interaction's trace_id.
# Both default to unset, in which case only the durable local JSONL audit is
# written (so the audit ALWAYS exists, even with no DB / no FG-16 merged).
_ChangeRecorder = Callable[[dict], None]
_TraceEmitter = Callable[[dict], None]

_sink_lock = threading.Lock()
_change_recorder: Optional[_ChangeRecorder] = None
_trace_emitter: Optional[_TraceEmitter] = None


def register_change_recorder(fn: Optional[_ChangeRecorder]) -> None:
    """Register (or clear) the C5 change-audit sink. Additive; degrades to local JSONL."""
    global _change_recorder
    with _sink_lock:
        _change_recorder = fn


def register_trace_emitter(fn: Optional[_TraceEmitter]) -> None:
    """Register (or clear) the C8 trace sink. Additive; degrades to no-op when unset."""
    global _trace_emitter
    with _sink_lock:
        _trace_emitter = fn


def _audit_home() -> Path:
    """Resolve HERMES_HOME (profile-aware) for the durable audit log."""
    try:
        from hermes_constants import get_hermes_home  # local import to avoid cycles
        return Path(get_hermes_home())
    except Exception:
        return Path(os.path.expanduser("~/.hermes"))


def core_audit_log_path() -> Path:
    """Path of the durable, append-only local Core-denial audit log (JSONL)."""
    return _audit_home() / "audit" / "core_boundary.jsonl"


def _resolve_mode() -> str:
    """Best-effort dev/prod mode for the audit row; ``"unknown"`` if unavailable."""
    try:
        from hermes_cli.datastore import resolve_mode
        return str(resolve_mode())
    except Exception:
        return "unknown"


def record_core_denied(
    *,
    path: str,
    matched_glob: str,
    op: str,
    actor_user_id: Optional[str] = None,
) -> dict:
    """Emit the audit for a denied Core write and return the audit event.

    Writes a durable, C5-shaped row to the local JSONL audit log (always — no DB
    required), then best-effort forwards to a registered C5 change recorder and
    C8 trace emitter (``core_denied``). Never raises into the write path.
    """
    event = {
        "id": f"chg_{uuid.uuid4().hex}",
        "ts": time.time(),
        "actor_user_id": actor_user_id or "runtime-agent",
        "mode": _resolve_mode(),
        "target_kind": "code",
        # C5 op shape: a non-reversible "core_write_denied" verb (nothing was
        # written, so there is no inverse and no backup).
        "op": {
            "kind": "core_write_denied",
            "op": op,
            "path": path,
            "matched_glob": matched_glob,
        },
        "inverse_op": None,
        "reversible": False,
        "approval_ref": None,
        "backup_ref": None,
        # C8 trace row kind.
        "kind": "core_denied",
        "summary": f"Refused agent {op} to Core path {path} (matched {matched_glob!r})",
    }

    # 1) Durable local audit — the guarantee. Best-effort; never raise.
    try:
        log_path = core_audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except Exception:
        pass

    # 2) Best-effort forward to richer sinks (FG-12 C5, FG-16 C8) when present.
    with _sink_lock:
        recorder = _change_recorder
        emitter = _trace_emitter
    if recorder is not None:
        try:
            recorder(event)
        except Exception:
            pass
    if emitter is not None:
        try:
            emitter(event)
        except Exception:
            pass

    return event


# ---------------------------------------------------------------------------
# The hard write-guard (the chokepoint entry point)
# ---------------------------------------------------------------------------

def check_core_write(
    path: str | os.PathLike[str],
    *,
    op: str = "write",
    actor_user_id: Optional[str] = None,
) -> Optional[str]:
    """Hard, fail-closed Core write-guard for the runtime agent's file tools.

    Returns ``None`` when the write is allowed (target is Customizable). When
    the resolved target is Core, emits the C5 audit + C8 trace (``core_denied``)
    and returns a clear denial message for the tool to surface to the model.
    There is no override.
    """
    matched = classify_core_path(path)
    if matched is None:
        return None
    record_core_denied(
        path=str(path), matched_glob=matched, op=op, actor_user_id=actor_user_id
    )
    return (
        f"Write denied: {str(path)!r} is a Core system path (matched "
        f"{matched!r} in core_manifest.yaml) and is immutable to the runtime "
        f"agent. Core is changed only by human developers through the repo/PR "
        f"flow — not by the agent, and there is no override. Put new capability "
        f"in the Customizable area instead (a skill, plugin, user tool, or "
        f"config.yaml). This attempt has been audited."
    )
