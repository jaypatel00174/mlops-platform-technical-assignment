"""Deployment endpoints. Every action that changes anything answers 202: the request is recorded
and a worker does the work, so the client polls `GET /deployments/{id}` for the outcome.

Roles are checked before any state is read. The service then applies the fine-grained rule
(production needs the release-manager role) once it knows the environment.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from mlops.deployment.api.schemas import (
    AcceptedOut,
    AttemptOut,
    BindingListOut,
    BindingOut,
    DeploymentCreate,
    DeploymentListOut,
    DeploymentOut,
    TimelineEntryOut,
    TimelineOut,
)
from mlops.deployment.domain.status import DeploymentStatus
from mlops.governance.application.access import Action, authorize
from mlops.kernel.errors import ValidationFailure
from mlops.platform.auth import CurrentPrincipal
from mlops.platform.problem import PROBLEM_RESPONSES
from mlops.wiring import Uow

router = APIRouter(prefix="/api/v1", tags=["deployments"], responses=PROBLEM_RESPONSES)

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "Required. 8 to 128 characters. The same key with the same body returns the original "
            "202; the same key with a different body is a 422. Keys expire after 24 hours."
        ),
    ),
]


def _location(deployment_id: str) -> str:
    return f"/api/v1/deployments/{deployment_id}"


@router.post(
    "/deployments",
    status_code=202,
    response_model=AcceptedOut,
    summary="Request a deployment (asynchronous)",
)
def request_deployment(
    body: DeploymentCreate,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> AcceptedOut:
    authorize(principal, Action.DEPLOY_NON_PRODUCTION)
    if idempotency_key is None:
        raise ValidationFailure("the Idempotency-Key header is required", field="Idempotency-Key")
    with uow.transaction(principal) as services:
        accepted = services.deployments.request_deployment(
            principal, idempotency_key=idempotency_key, **body.model_dump()
        )
    response.headers["Location"] = _location(accepted.deployment_id)
    if accepted.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return AcceptedOut(**accepted.body)


@router.get("/deployments", response_model=DeploymentListOut, summary="List deployments")
def list_deployments(
    principal: CurrentPrincipal,
    uow: Uow,
    model_id: Annotated[str | None, Query(max_length=63)] = None,
    environment: Annotated[str | None, Query(max_length=32)] = None,
    status: Annotated[DeploymentStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DeploymentListOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        found = services.deployments.search(
            model_id=model_id, environment=environment, status=status, limit=limit
        )
    return DeploymentListOut(items=[DeploymentOut.from_domain(d) for d in found])


@router.get(
    "/deployments/{deployment_id}",
    response_model=DeploymentOut,
    summary="Get a deployment: status, attempt, failure class",
)
def get_deployment(deployment_id: str, principal: CurrentPrincipal, uow: Uow) -> DeploymentOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        return DeploymentOut.from_domain(services.deployments.get(deployment_id))


@router.get(
    "/deployments/{deployment_id}/events",
    response_model=TimelineOut,
    summary="Timeline: attempts and every recorded change, oldest first",
)
def get_timeline(deployment_id: str, principal: CurrentPrincipal, uow: Uow) -> TimelineOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        deployment = services.deployments.get(deployment_id)
        attempts = services.deployments.attempts(deployment_id)
        history = services.audit.history("deployment", deployment_id)
    return TimelineOut(
        deployment=DeploymentOut.from_domain(deployment),
        attempts=[AttemptOut.from_domain(a) for a in attempts],
        events=[
            TimelineEntryOut(
                occurred_at=e.occurred_at,
                actor=e.actor,
                action=e.action,
                details=dict(e.after) if e.after is not None else None,
            )
            for e in history
        ],
    )


@router.post(
    "/deployments/{deployment_id}/retry",
    status_code=202,
    response_model=DeploymentOut,
    summary="Retry a FAILED deployment (a new attempt)",
)
def retry_deployment(
    deployment_id: str, principal: CurrentPrincipal, uow: Uow, response: Response
) -> DeploymentOut:
    authorize(principal, Action.DEPLOY_NON_PRODUCTION)
    with uow.transaction(principal) as services:
        retried = services.deployments.retry(principal, deployment_id)
    response.headers["Location"] = _location(retried.id)
    return DeploymentOut.from_domain(retried)


@router.post(
    "/deployments/{deployment_id}/rollback",
    status_code=202,
    response_model=DeploymentOut,
    summary="Roll back to the previously live version (a new ROLLBACK deployment)",
    description=(
        "Refused with 409 UNSAFE_ROLLBACK, listing every failed check in `details.failed_checks`, "
        "unless the previous deployment succeeded, is approved and not archived, its artifact "
        "digest verifies and the runtime supports it. The returned deployment is the rollback."
    ),
)
def rollback_deployment(
    deployment_id: str, principal: CurrentPrincipal, uow: Uow, response: Response
) -> DeploymentOut:
    authorize(principal, Action.DEPLOY_NON_PRODUCTION)
    with uow.transaction(principal) as services:
        rollback = services.deployments.rollback(principal, deployment_id)
    response.headers["Location"] = _location(rollback.id)
    return DeploymentOut.from_domain(rollback)


@router.get(
    "/environments/{environment}/bindings",
    response_model=BindingListOut,
    summary="What is live per model in an environment",
)
def list_bindings(environment: str, principal: CurrentPrincipal, uow: Uow) -> BindingListOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        bindings = services.deployments.bindings(environment)
    return BindingListOut(
        environment=environment, items=[BindingOut.from_domain(b) for b in bindings]
    )
