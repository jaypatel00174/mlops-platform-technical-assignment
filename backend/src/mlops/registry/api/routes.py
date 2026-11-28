"""Registry endpoints: models, versions and the actions on a version.

Approve and reject live here, not in the governance API, because they act on a version and return
its representation. Governance still decides: the route calls the approval service, which applies
role, separation-of-duties and stage rules. Roles are checked first so a caller without
permission learns nothing about the state of a resource.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from mlops.governance.application.access import Action, authorize
from mlops.kernel.concurrency import etag_for
from mlops.platform.auth import CurrentPrincipal
from mlops.platform.pagination import decode_cursor, encode_cursor
from mlops.platform.problem import PROBLEM_RESPONSES
from mlops.registry.api.schemas import (
    DecisionRequest,
    ModelCreate,
    ModelListOut,
    ModelOut,
    VersionCreate,
    VersionListOut,
    VersionOut,
)
from mlops.registry.application.services import ModelSummary, ModelVersion
from mlops.wiring import Uow

router = APIRouter(prefix="/api/v1", tags=["registry"], responses=PROBLEM_RESPONSES)

IfMatch = Annotated[
    str | None,
    Header(description="ETag from a previous read. Fails with 409 if the version has changed."),
]


def _version_response(version: ModelVersion, response: Response) -> VersionOut:
    response.headers["ETag"] = etag_for(version.row_version)
    return VersionOut.from_domain(version)


# ----------------------------------------------------------------------------------- models


@router.post("/models", status_code=201, response_model=ModelOut, summary="Register a model")
def register_model(
    body: ModelCreate, principal: CurrentPrincipal, uow: Uow, response: Response
) -> ModelOut:
    authorize(principal, Action.REGISTER_MODEL)
    with uow.transaction(principal) as services:
        model = services.registry.register_model(actor=principal.subject, **body.model_dump())
    response.headers["Location"] = f"/api/v1/models/{model.id}"
    return ModelOut.from_summary(ModelSummary(model, 0, None))


@router.get("/models", response_model=ModelListOut, summary="List and search models")
def list_models(
    principal: CurrentPrincipal,
    uow: Uow,
    q: Annotated[str | None, Query(max_length=100, description="Matches id or name")] = None,
    framework: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> ModelListOut:
    authorize(principal, Action.VIEW)
    after_id = decode_cursor(cursor)
    with uow.transaction(principal) as services:
        page = services.registry.list_models(
            search=q, framework=framework, limit=limit, after_id=after_id
        )
    return ModelListOut(
        items=[ModelOut.from_summary(item) for item in page.items],
        next_cursor=encode_cursor(page.next_after_id) if page.next_after_id else None,
    )


@router.get("/models/{model_id}", response_model=ModelOut, summary="Get a model")
def get_model(model_id: str, principal: CurrentPrincipal, uow: Uow) -> ModelOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        return ModelOut.from_summary(services.registry.get_model(model_id))


# --------------------------------------------------------------------------------- versions


@router.post(
    "/models/{model_id}/versions",
    status_code=201,
    response_model=VersionOut,
    summary="Register a version of a model",
)
def register_version(
    model_id: str,
    body: VersionCreate,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
) -> VersionOut:
    authorize(principal, Action.REGISTER_VERSION)
    with uow.transaction(principal) as services:
        version = services.registry.register_version(
            actor=principal.subject, model_id=model_id, **body.model_dump()
        )
    response.headers["Location"] = f"/api/v1/models/{model_id}/versions/{version.version}"
    return _version_response(version, response)


@router.get(
    "/models/{model_id}/versions",
    response_model=VersionListOut,
    summary="List the versions of a model, oldest first",
)
def list_versions(model_id: str, principal: CurrentPrincipal, uow: Uow) -> VersionListOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        versions = services.registry.list_versions(model_id)
    return VersionListOut(items=[VersionOut.from_domain(v) for v in versions])


@router.get(
    "/models/{model_id}/versions/{version}",
    response_model=VersionOut,
    summary="Get a version (returns an ETag)",
)
def get_version(
    model_id: str, version: str, principal: CurrentPrincipal, uow: Uow, response: Response
) -> VersionOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        found = services.registry.get_version(model_id, version)
    return _version_response(found, response)


@router.post(
    "/models/{model_id}/versions/{version}/validate",
    response_model=VersionOut,
    summary="Mark a draft version as validated",
)
def validate_version(
    model_id: str,
    version: str,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
    if_match: IfMatch = None,
) -> VersionOut:
    authorize(principal, Action.VALIDATE_VERSION)
    with uow.transaction(principal) as services:
        changed = services.registry.validate_version(
            actor=principal.subject, model_id=model_id, version=version, if_match=if_match
        )
    return _version_response(changed, response)


@router.post(
    "/models/{model_id}/versions/{version}/approve",
    response_model=VersionOut,
    summary="Approve a validated version",
    description="The approver must be a different person from whoever registered the version.",
)
def approve_version(
    model_id: str,
    version: str,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
    body: DecisionRequest | None = None,
    if_match: IfMatch = None,
) -> VersionOut:
    authorize(principal, Action.APPROVE_VERSION)
    with uow.transaction(principal) as services:
        changed = services.approvals.decide(
            principal=principal,
            model_id=model_id,
            version=version,
            approve=True,
            comment=body.comment if body else None,
            if_match=if_match,
        )
    return _version_response(changed, response)


@router.post(
    "/models/{model_id}/versions/{version}/reject",
    response_model=VersionOut,
    summary="Reject a validated version",
    description="A reason is required. The version stays VALIDATED and can be resubmitted.",
)
def reject_version(
    model_id: str,
    version: str,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
    body: DecisionRequest | None = None,
    if_match: IfMatch = None,
) -> VersionOut:
    authorize(principal, Action.APPROVE_VERSION)
    with uow.transaction(principal) as services:
        changed = services.approvals.decide(
            principal=principal,
            model_id=model_id,
            version=version,
            approve=False,
            comment=body.comment if body else None,
            if_match=if_match,
        )
    return _version_response(changed, response)


@router.post(
    "/models/{model_id}/versions/{version}/archive",
    response_model=VersionOut,
    summary="Archive a version that is not live anywhere",
)
def archive_version(
    model_id: str,
    version: str,
    principal: CurrentPrincipal,
    uow: Uow,
    response: Response,
    if_match: IfMatch = None,
) -> VersionOut:
    authorize(principal, Action.ARCHIVE_VERSION)
    with uow.transaction(principal) as services:
        changed = services.registry.archive_version(
            actor=principal.subject, model_id=model_id, version=version, if_match=if_match
        )
    return _version_response(changed, response)
