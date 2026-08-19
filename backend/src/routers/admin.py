from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..policy import policy_store
from ..security import require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/policy")
def get_policy() -> Dict[str, Any]:
    return policy_store.get()


@router.put("/policy")
def update_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    return policy_store.update(policy)
