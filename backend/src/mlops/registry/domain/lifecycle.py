"""The model version lifecycle as an explicit transition table.

    DRAFT -> VALIDATED -> APPROVED -> STAGING -> PRODUCTION -> ARCHIVED

Stage is never edited directly. It changes through `transition`, which is driven by governance
actions (validate, approve) and by deployment outcomes (`stage_after_successful_deployment`).
Any stage can be archived, but a version that is live in an environment cannot be.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from mlops.kernel import vocabulary as env
from mlops.kernel.errors import InvalidTransition, PolicyViolation
from mlops.kernel.vocabulary import Stage

_ALLOWED: Mapping[Stage, frozenset[Stage]] = {
    Stage.DRAFT: frozenset({Stage.VALIDATED, Stage.ARCHIVED}),
    Stage.VALIDATED: frozenset({Stage.APPROVED, Stage.ARCHIVED}),
    Stage.APPROVED: frozenset({Stage.STAGING, Stage.ARCHIVED}),
    Stage.STAGING: frozenset({Stage.PRODUCTION, Stage.ARCHIVED}),
    Stage.PRODUCTION: frozenset({Stage.ARCHIVED}),
    Stage.ARCHIVED: frozenset(),
}


def allowed_targets(current: Stage) -> frozenset[Stage]:
    return _ALLOWED[current]


def can_transition(current: Stage, target: Stage) -> bool:
    return target in _ALLOWED[current]


def transition(current: Stage, target: Stage) -> Stage:
    """Return `target` if the move is legal, otherwise raise InvalidTransition."""
    if not can_transition(current, target):
        raise InvalidTransition(
            f"cannot move a version from {current.value} to {target.value}",
            current=current.value,
            target=target.value,
            allowed=sorted(stage.value for stage in _ALLOWED[current]),
        )
    return target


def archive(current: Stage, live_environments: Collection[str]) -> Stage:
    """Archive a version. Refused while the version is live in any environment."""
    if live_environments:
        raise PolicyViolation(
            "a version that is live in an environment cannot be archived",
            live_in=sorted(live_environments),
        )
    return transition(current, Stage.ARCHIVED)


def stage_after_successful_deployment(current: Stage, environment: str) -> Stage:
    """The stage a version reaches after a deployment to `environment` succeeds.

    Deploying to staging promotes APPROVED to STAGING; deploying to production promotes STAGING
    to PRODUCTION. Staging cannot be skipped. Redeploying (idempotent retry, or rolling back to a
    version that was already PRODUCTION) never demotes. Other environments, such as `dev`, are not
    governed and leave the stage unchanged, but an ARCHIVED version can never be deployed.
    """
    if current is Stage.ARCHIVED:
        raise InvalidTransition(
            "an archived version cannot be deployed",
            current=current.value,
            target=environment,
            allowed=[],
        )
    if environment == env.STAGING:
        if current in (Stage.STAGING, Stage.PRODUCTION):
            return current
        return transition(current, Stage.STAGING)
    if environment == env.PRODUCTION:
        if current is Stage.PRODUCTION:
            return current
        return transition(current, Stage.PRODUCTION)
    return current
