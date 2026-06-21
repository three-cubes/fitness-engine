"""CORE check: no_logging_secrets — never log a secret-named value in plaintext.

A variable whose name strongly implies it holds a secret (``api_key``,
``token``, ``password``, ``client_secret``, ``bearer``, ``jwt``,
``private_key``) passed to a logging / print / exception sink without
redaction is a recurring incident class: boot-time credential reveals,
exception messages echoing the value to operators, debug prints left over
from an investigation. The cure is cheap (log ``token is not None`` or
``len(token)``); the cost of the leak is a credential rotation.

Ported from tc-agent-zone ``scripts/checks/no_logging_secrets.py`` (itself
kairix F15) and re-expressed as a configurable, repo-agnostic rule. The AST
detection is already domain-intrinsic; what was repo-specific (the scanned
source roots, the boundary modules that own redaction and are exempt) is now
consumer config. The secret-name patterns and sink surface ship as
domain-intrinsic defaults (the shape of "a secret reaching a log sink"), every
one overridable via ``[tool.tc_fitness]``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tc_fitness.core_checks import run_core_check
from tc_fitness.fitness_rule import FitnessRule
from tc_fitness.lib import remediation as _remediation

#: Identifier patterns implying a secret value — matched against the trailing
#: segment of a Name/Attribute (``self.api_key`` -> ``api_key``). Domain-
#: intrinsic (the shape of a secret name), overridable via config.
DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    r"^api_key$",
    r".*_api_key$",
    r"^token$",
    r".*_token$",
    r"^secret$",
    r".*_secret$",
    r"^password$",
    r".*_password$",
    r"^credential$|^credentials$",
    r".*_credentials?$",
    r"^bearer$",
    r"^jwt$",
    r".*_jwt$",
    r"^private_key$",
    r".*_private_key$",
)

#: Logger / sink method names (``logger.info`` / ``log.debug``). The receiver
#: is ignored, so renamed loggers are still caught.
DEFAULT_LOG_METHODS: frozenset[str] = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}
)

#: Direct function-call sinks.
DEFAULT_DIRECT_SINKS: frozenset[str] = frozenset({"print"})

REMEDIATION = _remediation(
    fix=(
        "rewrite each flagged log/print/raise call so the secret-named value is "
        "summarised before it reaches the sink — pass `api_key is not None` (a "
        "bool), `len(token)` (an int), or a non-secret correlation key. If the "
        "call legitimately handles a secret, move it inside one of the configured "
        "redaction-boundary modules (the exempt_files)."
    ),
    nxt="re-run this check to confirm the gate goes green.",
    run="python -m tc_fitness.core_checks.no_logging_secrets",
    passing='logger.info("api_key present: %s", api_key is not None)',
    forbidden='logger.info("api key is %s", api_key)  # passes the raw secret',
)


def _trailing_name(node: ast.expr) -> str | None:
    """The trailing identifier of a Name/Attribute (``a.b.c`` -> ``c``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _looks_like_secret(name: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.match(name) for p in patterns)


def _arg_references_secret(arg: ast.expr, patterns: Sequence[re.Pattern[str]]) -> bool:
    """True iff ``arg`` exposes a secret-named identifier to a surrounding sink.

    Handles bare Names, Attributes, f-string interpolations
    (``JoinedStr -> FormattedValue``), and single-arg wrappers (``str(token)``).
    """
    leaf = _trailing_name(arg)
    if leaf is not None:
        return _looks_like_secret(leaf, patterns)
    if isinstance(arg, ast.JoinedStr):
        return any(
            isinstance(part, ast.FormattedValue) and _arg_references_secret(part.value, patterns)
            for part in arg.values
        )
    if isinstance(arg, ast.Call) and len(arg.args) == 1 and not arg.keywords:
        return _arg_references_secret(arg.args[0], patterns)
    return False


def _is_log_call(call: ast.Call, log_methods: frozenset[str]) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr in log_methods


def _is_direct_sink_call(call: ast.Call, direct_sinks: frozenset[str]) -> bool:
    if isinstance(call.func, ast.Name) and call.func.id in direct_sinks:
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr == "write":
        value = call.func.value
        if isinstance(value, ast.Attribute) and value.attr in {"stdout", "stderr"}:
            return True
    return False


def _sink_call_leaks_secret(
    call: ast.Call,
    *,
    patterns: Sequence[re.Pattern[str]],
    log_methods: frozenset[str],
    direct_sinks: frozenset[str],
) -> bool:
    if not (_is_log_call(call, log_methods) or _is_direct_sink_call(call, direct_sinks)):
        return False
    if any(_arg_references_secret(a, patterns) for a in call.args):
        return True
    return any(kw.value is not None and _arg_references_secret(kw.value, patterns) for kw in call.keywords)


def _raise_leaks_secret(node: ast.Raise, patterns: Sequence[re.Pattern[str]]) -> bool:
    if not isinstance(node.exc, ast.Call):
        return False
    return any(_arg_references_secret(a, patterns) for a in node.exc.args)


def module_logs_secret(
    path: Path,
    *,
    patterns: Sequence[re.Pattern[str]],
    log_methods: frozenset[str],
    direct_sinks: frozenset[str],
) -> bool:
    """Pure detection helper: True iff any sink in ``path`` leaks a secret-named arg.

    A syntax / decode / read error is treated as "no violation" (another check
    owns unparseable files).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _sink_call_leaks_secret(
            node, patterns=patterns, log_methods=log_methods, direct_sinks=direct_sinks
        ):
            return True
        if isinstance(node, ast.Raise) and _raise_leaks_secret(node, patterns):
            return True
    return False


class NoLoggingSecrets(FitnessRule):
    """Flags modules that pass a secret-named value to a log / print / raise sink."""

    name = "no-logging-secrets"
    remediation = REMEDIATION
    extensions = (".py",)

    #: Rule-specific config (instance attrs; from_config overrides per consumer).
    secret_patterns: tuple[re.Pattern[str], ...] = ()
    log_methods: frozenset[str] = DEFAULT_LOG_METHODS
    direct_sinks: frozenset[str] = DEFAULT_DIRECT_SINKS

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
    ) -> NoLoggingSecrets:
        rule = super().from_config(config, repo_root=repo_root)
        assert isinstance(rule, NoLoggingSecrets)  # noqa: S101  # narrowing for mypy
        raw_patterns = config.get("secret_patterns", DEFAULT_SECRET_PATTERNS)
        rule.secret_patterns = tuple(re.compile(p) for p in raw_patterns)
        log_methods = config.get("log_methods")
        if log_methods is not None:
            rule.log_methods = frozenset(log_methods)
        direct_sinks = config.get("direct_sinks")
        if direct_sinks is not None:
            rule.direct_sinks = frozenset(direct_sinks)
        return rule

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.secret_patterns:
            self.secret_patterns = tuple(re.compile(p) for p in DEFAULT_SECRET_PATTERNS)

    def file_has_violation(self, path: Path) -> bool:
        return module_logs_secret(
            path,
            patterns=self.secret_patterns,
            log_methods=self.log_methods,
            direct_sinks=self.direct_sinks,
        )


def build(
    config: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> NoLoggingSecrets:
    """Factory the engine calls to bind this CORE check to a consumer's config."""
    return NoLoggingSecrets.from_config(config, repo_root=repo_root)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — supports ``--establish-baseline`` and ``--repo-root``."""
    return run_core_check(NoLoggingSecrets, argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
