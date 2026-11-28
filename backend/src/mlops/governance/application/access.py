"""Authorization entry point for other modules' API layers.

They may not import governance.domain, so the action vocabulary and the check are exposed here.
"""

from __future__ import annotations

from mlops.governance.domain.rbac import Action, require
from mlops.kernel.identity import Principal

__all__ = ["Action", "authorize"]


def authorize(principal: Principal, action: Action) -> None:
    """Raise Forbidden unless the caller's roles allow `action`."""
    require(principal.roles, action)
