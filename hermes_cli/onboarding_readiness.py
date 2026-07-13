"""FG-15 — typed onboarding setup schema + readiness scoring backend.

Single backend shared by ``hermes setup`` (CLI), ``hermes status``, and the
dashboard first-run wizard (UI deferred to FG-17, but this backend + the
``/api/onboarding/readiness`` endpoint are ready for it to consume).

The schema marks each setup item **required** or **optional** and gives every
one a *check*, a *fix action*, and a one-line *rationale*. A computed readiness
score gates the "ready for prod" state on all **required** items being met.

Design constraints (see ``docs/design/master-plan/README.md`` §2 and
``AGENTS.md``):

- **Secrets vs behaviour.** This module reads secret *presence* from the
  environment (never values) and behavioural state from ``config.yaml`` /
  the C3 datastore router. It introduces **no** new ``HERMES_*`` env vars.
- **Extend, don't duplicate.** Reuses C1 (owner store), C3 (datastore router),
  the existing ``onboarding.seen`` flags for resumability, and C8 for tracing.
- **Cache-safe.** Readiness/trace data is observability-only and is never fed
  back into a model prompt.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, cast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal sources (env var names — presence only, values never read/logged)
# ---------------------------------------------------------------------------

# LLM provider secrets — presence of ANY satisfies the required item. DeepSeek
# leads (the ai-prentice deployment's default provider). Mirrors the provider
# keys ``hermes status`` already surfaces; NOT a new env var.
LLM_PROVIDER_SECRET_ENV_VARS: tuple[str, ...] = (
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
    "GLM_API_KEY",
    "KIMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "NVIDIA_API_KEY",
    "STEPFUN_API_KEY",
)

TELEGRAM_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
TELEGRAM_HOME_CHANNEL_ENV_VAR = "TELEGRAM_HOME_CHANNEL"

# Non-Telegram channel credentials — presence of ANY satisfies the OPTIONAL
# "additional channel" item. Mirrors the ``hermes status`` platform table.
ADDITIONAL_CHANNEL_ENV_VARS: tuple[str, ...] = (
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "WHATSAPP_ENABLED",
    "SIGNAL_HTTP_URL",
    "EMAIL_ADDRESS",
    "TWILIO_ACCOUNT_SID",
    "DINGTALK_CLIENT_ID",
    "FEISHU_APP_ID",
    "WECOM_BOT_ID",
    "WEIXIN_ACCOUNT_ID",
    "BLUEBUBBLES_SERVER_URL",
    "QQ_APP_ID",
    "YUANBAO_APP_ID",
)

_UNRESOLVED_ENV_REF = re.compile(r"\$\{[^}]+\}")


# ---------------------------------------------------------------------------
# Item keys (stable — reused as onboarding.seen flags via seen_flag())
# ---------------------------------------------------------------------------

HOME_BOOTSTRAP = "home_bootstrap"
LLM_PROVIDER_SECRET = "llm_provider_secret"
APP_DATASTORE_DSN = "app_datastore_dsn"
OWNER_IDENTITY = "owner_identity"
TELEGRAM_CHANNEL = "telegram_channel"

ADDITIONAL_CHANNELS = "additional_channels"
MEMORY_PROVIDER = "memory_provider"
EXTRA_TOOLS = "extra_tools"


def seen_flag(item_key: str) -> str:
    """Return the ``onboarding.seen`` flag name recording an item's setup.

    Namespaced under ``setup_`` so it never collides with the first-touch hint
    flags in :mod:`agent.onboarding` (``busy_input_prompt`` etc.).
    """
    return f"setup_{item_key}_done"


# ---------------------------------------------------------------------------
# Typed setup schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupItem:
    """One item in the onboarding setup schema (required or optional)."""

    key: str
    label: str
    required: bool
    rationale: str
    fix_command: str
    contract: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "required": self.required,
            "rationale": self.rationale,
            "fix_command": self.fix_command,
            "contract": self.contract,
        }


# The essential 5 REQUIRED items, then the OPTIONAL ones. Ordered so a fix flow
# configures prerequisites first (home → datastore → owner → provider → channel).
SETUP_SCHEMA: tuple[SetupItem, ...] = (
    SetupItem(
        key=HOME_BOOTSTRAP,
        label="Hermes home & config",
        required=True,
        rationale="The profile dir + config.yaml hold every behavioural setting.",
        fix_command="hermes setup",
    ),
    SetupItem(
        key=APP_DATASTORE_DSN,
        label="Application datastore (Supabase)",
        required=True,
        rationale="Identity, memory, goals/tasks and traces live in the app DB.",
        fix_command="hermes setup essentials",
        contract="C3",
    ),
    SetupItem(
        key=OWNER_IDENTITY,
        label="Owner identity",
        required=True,
        rationale="Exactly one owner bootstraps access control and sees everything.",
        fix_command="hermes owner init <user_id>",
        contract="C1",
    ),
    SetupItem(
        key=LLM_PROVIDER_SECRET,
        label="LLM provider secret",
        required=True,
        rationale="Without an inference key the agent cannot think or reply.",
        fix_command="hermes setup model",
    ),
    SetupItem(
        key=TELEGRAM_CHANNEL,
        label="Telegram channel",
        required=True,
        rationale="At least one conversational channel is needed to talk to the agent.",
        fix_command="hermes setup gateway",
    ),
    SetupItem(
        key=ADDITIONAL_CHANNELS,
        label="Additional channels",
        required=False,
        rationale="Reach the agent from WhatsApp/email/Discord and other platforms.",
        fix_command="hermes setup gateway",
    ),
    SetupItem(
        key=MEMORY_PROVIDER,
        label="External memory provider",
        required=False,
        rationale="An external memory backend adds durable recall beyond the built-in store.",
        fix_command="hermes memory setup",
    ),
    SetupItem(
        key=EXTRA_TOOLS,
        label="Extra tools",
        required=False,
        rationale="Enable web/browser/image and other toolsets to broaden capability.",
        fix_command="hermes tools",
    ),
)

REQUIRED_ITEMS: tuple[SetupItem, ...] = tuple(i for i in SETUP_SCHEMA if i.required)
OPTIONAL_ITEMS: tuple[SetupItem, ...] = tuple(i for i in SETUP_SCHEMA if not i.required)


def get_item(key: str) -> SetupItem:
    """Return the schema item for ``key`` (raises ``KeyError`` if unknown)."""
    for item in SETUP_SCHEMA:
        if item.key == key:
            return item
    raise KeyError(key)


# ---------------------------------------------------------------------------
# Signals — the raw environment/config/owner facts each item is checked against
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadinessSignals:
    """Resolved, side-effect-free facts used to evaluate the setup schema."""

    home_bootstrapped: bool = False
    llm_secret_present: bool = False
    llm_secret_name: str = ""
    app_dsn_present: bool = False
    owner_enrolled: bool = False
    owner_detail: str = ""
    telegram_token_present: bool = False
    telegram_channel_bound: bool = False
    additional_channel_present: bool = False
    memory_provider_present: bool = False
    extra_tools_present: bool = False


def _secret_present(env: Mapping[str, str], name: str) -> bool:
    value = (env.get(name) or "").strip()
    return bool(value) and not _UNRESOLVED_ENV_REF.search(value)


def _value_present(value: object) -> bool:
    """True when ``value`` is a non-empty string with no unresolved env ref."""
    if not isinstance(value, str):
        return bool(value)
    text = value.strip()
    return bool(text) and not _UNRESOLVED_ENV_REF.search(text)


def _first_present_secret(
    env: Mapping[str, str], names: Sequence[str]
) -> str:
    for name in names:
        if _secret_present(env, name):
            return name
    return ""


def _resolve_app_dsn(config: Mapping[str, object] | None) -> bool:
    """Return True when a Supabase app DSN is effectively configured (any mode)."""
    try:
        from hermes_cli.datastore import get_store

        for mode in ("prod", "dev"):
            store = get_store("supabase-app", mode, config=config)
            if _value_present(store.dsn):
                return True
            # The config may carry an unexpanded ${VAR} template (e.g. the
            # wizard just wrote the DSN to .env and stored only a reference).
            # Expand against the live env so presence reflects resolvability.
            if isinstance(store.dsn, str) and _value_present(
                os.path.expandvars(store.dsn)
            ):
                return True
    except Exception as exc:  # pragma: no cover — defensive; never block status
        logger.debug("readiness: app DSN probe failed: %s", exc)
    return False


def app_datastore_configured(config: Mapping[str, object] | None = None) -> bool:
    """True when a Supabase app DSN is effectively configured (any mode).

    Public helper reused by the ``hermes setup`` wizard to gate owner
    enrollment (the owner principal lives in the app datastore).
    """
    resolved = config if config is not None else _load_config()
    return _resolve_app_dsn(resolved)


def _memory_provider_present(config: Mapping[str, object] | None) -> bool:
    if not isinstance(config, Mapping):
        return False
    memory = config.get("memory")
    if not isinstance(memory, Mapping):
        return False
    return _value_present(cast(Mapping[str, object], memory).get("provider"))


def _extra_tools_present(config: Mapping[str, object] | None) -> bool:
    if not isinstance(config, Mapping):
        return False
    toolsets = config.get("toolsets")
    if not isinstance(toolsets, list):
        return False
    extras = [t for t in toolsets if isinstance(t, str) and t and t != "hermes-cli"]
    return bool(extras)


def _hermes_home(hermes_home: Optional[Path]) -> Path:
    if hermes_home is not None:
        return hermes_home
    from hermes_constants import get_hermes_home

    return get_hermes_home()


async def probe_owner_enrolled(
    config: Mapping[str, object] | None = None,
) -> tuple[bool, str]:
    """Best-effort C1 owner probe. Never raises; returns ``(enrolled, detail)``."""
    try:
        from hermes_cli.access import PrincipalStore
        from hermes_cli.datastore import get_store

        store = PrincipalStore(get_store("supabase-app", "prod", config=config))
        owner = await store.get_owner()
    except Exception as exc:
        return False, f"owner unknown ({type(exc).__name__})"
    if owner is None:
        return False, "no owner enrolled"
    return True, f"owner: {owner.user_id}"


def _probe_owner_sync(config: Mapping[str, object] | None) -> tuple[bool, str]:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to drive the async probe ourselves.
        try:
            return asyncio.run(probe_owner_enrolled(config))
        except Exception as exc:  # pragma: no cover — defensive
            return False, f"owner unknown ({type(exc).__name__})"
    # A loop is already running (async caller) — use evaluate_async instead.
    return False, "owner probe deferred (use async path)"


def _gather_common(
    config: Mapping[str, object] | None,
    env: Mapping[str, str],
    hermes_home: Optional[Path],
) -> dict[str, object]:
    home = _hermes_home(hermes_home)
    llm_name = _first_present_secret(env, LLM_PROVIDER_SECRET_ENV_VARS)
    app_dsn = _resolve_app_dsn(config)
    return {
        "home_bootstrapped": (home / "config.yaml").is_file(),
        "llm_secret_present": bool(llm_name),
        "llm_secret_name": llm_name,
        "app_dsn_present": app_dsn,
        "telegram_token_present": _secret_present(env, TELEGRAM_TOKEN_ENV_VAR),
        "telegram_channel_bound": _secret_present(env, TELEGRAM_HOME_CHANNEL_ENV_VAR),
        "additional_channel_present": any(
            _secret_present(env, name) for name in ADDITIONAL_CHANNEL_ENV_VARS
        ),
        "memory_provider_present": _memory_provider_present(config),
        "extra_tools_present": _extra_tools_present(config),
    }


def gather_signals(
    config: Mapping[str, object] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    hermes_home: Optional[Path] = None,
    include_owner: bool = True,
) -> ReadinessSignals:
    """Resolve all signals synchronously (owner probe via a private loop)."""
    resolved_env = env if env is not None else os.environ
    resolved_config = config if config is not None else _load_config()
    common = _gather_common(resolved_config, resolved_env, hermes_home)
    owner_enrolled, owner_detail = (False, "not checked")
    if include_owner and common["app_dsn_present"]:
        owner_enrolled, owner_detail = _probe_owner_sync(resolved_config)
    elif not common["app_dsn_present"]:
        owner_detail = "app datastore not configured"
    return ReadinessSignals(
        owner_enrolled=owner_enrolled,
        owner_detail=owner_detail,
        **common,  # type: ignore[arg-type]
    )


async def gather_signals_async(
    config: Mapping[str, object] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    hermes_home: Optional[Path] = None,
    include_owner: bool = True,
) -> ReadinessSignals:
    """Async signal resolution (awaits the owner probe directly)."""
    resolved_env = env if env is not None else os.environ
    resolved_config = config if config is not None else _load_config()
    common = _gather_common(resolved_config, resolved_env, hermes_home)
    owner_enrolled, owner_detail = (False, "not checked")
    if include_owner and common["app_dsn_present"]:
        owner_enrolled, owner_detail = await probe_owner_enrolled(resolved_config)
    elif not common["app_dsn_present"]:
        owner_detail = "app datastore not configured"
    return ReadinessSignals(
        owner_enrolled=owner_enrolled,
        owner_detail=owner_detail,
        **common,  # type: ignore[arg-type]
    )


def _load_config() -> Mapping[str, object]:
    try:
        from hermes_cli.config import load_config_readonly

        loaded = load_config_readonly()
        return loaded if isinstance(loaded, Mapping) else {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("readiness: config load failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Item evaluation + readiness score
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ItemResult:
    """The evaluated status of one :class:`SetupItem`."""

    item: SetupItem
    met: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        data = self.item.as_dict()
        data["met"] = self.met
        data["detail"] = self.detail
        return data


def _evaluate_item(item: SetupItem, s: ReadinessSignals) -> ItemResult:
    if item.key == HOME_BOOTSTRAP:
        return ItemResult(
            item,
            s.home_bootstrapped,
            "config.yaml present" if s.home_bootstrapped else "config.yaml missing",
        )
    if item.key == APP_DATASTORE_DSN:
        return ItemResult(
            item,
            s.app_dsn_present,
            "DSN configured" if s.app_dsn_present else "DSN unset",
        )
    if item.key == OWNER_IDENTITY:
        return ItemResult(item, s.owner_enrolled, s.owner_detail)
    if item.key == LLM_PROVIDER_SECRET:
        return ItemResult(
            item,
            s.llm_secret_present,
            f"{s.llm_secret_name} set" if s.llm_secret_present else "no provider key set",
        )
    if item.key == TELEGRAM_CHANNEL:
        met = s.telegram_token_present and s.telegram_channel_bound
        if met:
            detail = "bot token + home channel bound"
        elif s.telegram_token_present:
            detail = "bot token set, no home channel bound"
        else:
            detail = "bot token not set"
        return ItemResult(item, met, detail)
    if item.key == ADDITIONAL_CHANNELS:
        return ItemResult(
            item,
            s.additional_channel_present,
            "configured" if s.additional_channel_present else "none configured",
        )
    if item.key == MEMORY_PROVIDER:
        return ItemResult(
            item,
            s.memory_provider_present,
            "configured" if s.memory_provider_present else "built-in only",
        )
    if item.key == EXTRA_TOOLS:
        return ItemResult(
            item,
            s.extra_tools_present,
            "enabled" if s.extra_tools_present else "baseline only",
        )
    raise KeyError(item.key)  # pragma: no cover — schema/eval kept in sync


@dataclass(frozen=True)
class Readiness:
    """A computed readiness snapshot over the whole setup schema."""

    results: tuple[ItemResult, ...]

    def _split(self, required: bool) -> tuple[ItemResult, ...]:
        return tuple(r for r in self.results if r.item.required == required)

    @property
    def required_total(self) -> int:
        return len(self._split(True))

    @property
    def required_met(self) -> int:
        return sum(1 for r in self._split(True) if r.met)

    @property
    def optional_total(self) -> int:
        return len(self._split(False))

    @property
    def optional_met(self) -> int:
        return sum(1 for r in self._split(False) if r.met)

    @property
    def score(self) -> float:
        """Required-item completion ratio in ``[0.0, 1.0]``."""
        if self.required_total == 0:
            return 1.0
        return self.required_met / self.required_total

    @property
    def score_pct(self) -> int:
        return round(self.score * 100)

    @property
    def optional_coverage(self) -> float:
        if self.optional_total == 0:
            return 1.0
        return self.optional_met / self.optional_total

    @property
    def ready_for_prod(self) -> bool:
        """True only when EVERY required item is met (the prod gate)."""
        return self.required_met == self.required_total

    def missing_required(self) -> tuple[ItemResult, ...]:
        return tuple(r for r in self._split(True) if not r.met)

    def as_dict(self) -> dict[str, object]:
        return {
            "score": round(self.score, 4),
            "score_pct": self.score_pct,
            "ready_for_prod": self.ready_for_prod,
            "required_total": self.required_total,
            "required_met": self.required_met,
            "optional_total": self.optional_total,
            "optional_met": self.optional_met,
            "optional_coverage": round(self.optional_coverage, 4),
            "missing_required": [r.item.key for r in self.missing_required()],
            "items": [r.as_dict() for r in self.results],
        }


def compute_readiness(signals: ReadinessSignals) -> Readiness:
    """Evaluate the setup schema against pre-resolved ``signals``."""
    return Readiness(tuple(_evaluate_item(item, signals) for item in SETUP_SCHEMA))


def evaluate(
    config: Mapping[str, object] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    hermes_home: Optional[Path] = None,
    include_owner: bool = True,
) -> Readiness:
    """Gather signals synchronously and compute readiness."""
    return compute_readiness(
        gather_signals(
            config, env=env, hermes_home=hermes_home, include_owner=include_owner
        )
    )


async def evaluate_async(
    config: Mapping[str, object] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    hermes_home: Optional[Path] = None,
    include_owner: bool = True,
) -> Readiness:
    """Async variant of :func:`evaluate` (safe inside a running event loop)."""
    return compute_readiness(
        await gather_signals_async(
            config, env=env, hermes_home=hermes_home, include_owner=include_owner
        )
    )


# ---------------------------------------------------------------------------
# C8 trace (observability-only; no-op without an app datastore DSN)
# ---------------------------------------------------------------------------

def emit_readiness_trace(
    readiness: Readiness,
    *,
    actor_user_id: str,
    config: Mapping[str, object] | None = None,
    session_key: str = "cli:setup",
    platform: str = "local",
) -> bool:
    """Append a best-effort C8 interaction summarising an onboarding run.

    Emits one ``turn`` event (the readiness summary) plus one ``change`` event
    per met required item. No-op — returning ``False`` — when action tracking is
    disabled or the app datastore DSN is unset. Never raises and never touches a
    live model prompt (observability-only, so prompt caching is unaffected).
    """
    import asyncio

    try:
        resolved_config = config if config is not None else _load_config()
        from hermes_cli.datastore import SupabaseAppStore, get_store
        from hermes_cli.interactions import (
            ActionTrackingConfig,
            InteractionLedger,
            InteractionTrace,
        )

        settings = ActionTrackingConfig.from_config(resolved_config)
        if not settings.enabled:
            return False
        store = get_store("supabase-app", "prod", config=resolved_config)
        if not isinstance(store, SupabaseAppStore) or not _value_present(store.dsn):
            return False

        trace = InteractionTrace(
            actor_user_id=actor_user_id,
            session_key=session_key,
            platform=platform,
            mode=store.mode,
            sample=settings.sample,
        )
        trace.emit(
            "turn",
            ref="onboarding_readiness",
            summary=(
                f"onboarding readiness {readiness.score_pct}% "
                f"({readiness.required_met}/{readiness.required_total} required); "
                f"ready_for_prod={readiness.ready_for_prod}"
            ),
        )
        for result in readiness.results:
            if result.item.required and result.met:
                trace.emit(
                    "change",
                    ref=seen_flag(result.item.key),
                    summary=f"required item met: {result.item.label}",
                )

        ledger = InteractionLedger(store, config=resolved_config)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(ledger.append_many(trace.events))
            return True
        # A loop is already running — skip rather than nest run().
        logger.debug("readiness: trace skipped (running loop)")
        return False
    except Exception as exc:  # pragma: no cover — tracing is best-effort
        logger.debug("readiness: emit trace failed: %s", exc)
        return False


__all__ = [
    "SetupItem",
    "ItemResult",
    "Readiness",
    "ReadinessSignals",
    "SETUP_SCHEMA",
    "REQUIRED_ITEMS",
    "OPTIONAL_ITEMS",
    "HOME_BOOTSTRAP",
    "APP_DATASTORE_DSN",
    "OWNER_IDENTITY",
    "LLM_PROVIDER_SECRET",
    "TELEGRAM_CHANNEL",
    "ADDITIONAL_CHANNELS",
    "MEMORY_PROVIDER",
    "EXTRA_TOOLS",
    "LLM_PROVIDER_SECRET_ENV_VARS",
    "seen_flag",
    "get_item",
    "app_datastore_configured",
    "gather_signals",
    "gather_signals_async",
    "probe_owner_enrolled",
    "compute_readiness",
    "evaluate",
    "evaluate_async",
    "emit_readiness_trace",
]
