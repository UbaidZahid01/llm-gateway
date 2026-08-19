from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..security import require_admin
from ..storage import audit_store

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


@router.get("/logs")
def list_logs(client_ip: Optional[str] = None, provider: Optional[str] = None):
    logs = audit_store.read_all()
    if client_ip:
        logs = [log for log in logs if log["client_ip"] == client_ip]
    if provider:
        logs = [log for log in logs if log["provider"] == provider]
    return sorted(logs, key=lambda log: log["created_at"], reverse=True)


@router.get("/logs/{log_id}")
def get_log(log_id: str):
    for log in audit_store.read_all():
        if log["log_id"] == log_id:
            return log
    raise HTTPException(status_code=404, detail="Log not found")
