"""Rule registry.

Every rule is a small, independently readable function that returns 0..n
candidate recommendations. There is no big ``if`` tree anywhere — conflicts are
resolved centrally by :mod:`..arbiter`.

Rules are grouped into modules by domain (safety, air quality, moisture,
summer, winter, covers, general) rather than one file per rule; the registry
still addresses each rule individually by id, so rules can be enabled,
disabled and tested one at a time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
from typing import Any

from ..context import EvaluationContext
from ..state import Action, Priority, Recommendation, Season

_LOGGER = logging.getLogger(__name__)

RuleFunction = Callable[[EvaluationContext], Iterable[Recommendation]]


@dataclass(frozen=True)
class RuleDefinition:
    """One registered rule."""

    id: str
    priority: Priority
    func: RuleFunction
    seasons: frozenset[Season] = field(default_factory=lambda: frozenset(Season))
    description: str = ""

    def applies(self, season: Season) -> bool:
        return season in self.seasons

    def evaluate(self, ctx: EvaluationContext) -> list[Recommendation]:
        return list(self.func(ctx))


REGISTRY: dict[str, RuleDefinition] = {}


def rule(
    rule_id: str,
    priority: Priority,
    *,
    seasons: Iterable[Season] | None = None,
    description: str = "",
) -> Callable[[RuleFunction], RuleFunction]:
    """Register a rule function under ``rule_id``."""

    def decorator(func: RuleFunction) -> RuleFunction:
        if rule_id in REGISTRY:
            raise ValueError(f"duplicate rule id: {rule_id}")
        REGISTRY[rule_id] = RuleDefinition(
            id=rule_id,
            priority=priority,
            func=func,
            seasons=frozenset(seasons) if seasons else frozenset(Season),
            description=description or (func.__doc__ or "").strip().split("\n")[0],
        )
        return func

    return decorator


def make(
    ctx: EvaluationContext,
    rule_id: str,
    target: str,
    action: Action,
    priority: Priority,
    reason_key: str,
    *,
    urgency: int = 50,
    room_id: str | None = None,
    confidence: float = 1.0,
    **kwargs: Any,
) -> Recommendation:
    """Construct a recommendation with a stable, dedupe-friendly id."""
    return Recommendation(
        id=f"{rule_id}:{target}",
        target=target,
        action=action,
        priority=priority,
        urgency=max(0, min(100, urgency)),
        reason_key=reason_key,
        rule_id=rule_id,
        room_id=room_id,
        confidence=confidence,
        valid_from=kwargs.pop("valid_from", ctx.now),
        **kwargs,
    )


def run_rules(
    ctx: EvaluationContext, *, disabled: Iterable[str] = ()
) -> tuple[list[Recommendation], dict[str, str]]:
    """Run every applicable rule. Returns candidates plus per-rule errors."""
    disabled_set = set(disabled)
    candidates: list[Recommendation] = []
    errors: dict[str, str] = {}

    for definition in REGISTRY.values():
        if definition.id in disabled_set or not definition.applies(ctx.season):
            continue
        try:
            candidates.extend(definition.evaluate(ctx))
        except Exception as err:  # pragma: no cover - defensive, one rule must not kill the run
            _LOGGER.exception("rule %s failed", definition.id)
            errors[definition.id] = f"{type(err).__name__}: {err}"
    return candidates, errors


def load_all() -> None:
    """Import every rule module so the registry is populated."""
    from . import (  # noqa: F401  pylint: disable=import-outside-toplevel,cyclic-import
        air_quality,
        covers,
        general,
        moisture,
        safety,
        summer,
        winter,
    )


load_all()

__all__ = ["REGISTRY", "RuleDefinition", "make", "rule", "run_rules"]
