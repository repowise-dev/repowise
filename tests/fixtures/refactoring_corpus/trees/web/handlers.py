# Archetype: a framework-registered router module.
#
# Every function here is reachable only because a decorator registered it with
# a router. Nothing in the call graph calls them, and moving one to another
# module changes the import path the registry was populated from. The correct
# composed answer is that any step which *moves* one of these is a judgment
# call, never mechanical: the layer cannot see the registration, and says so.
#
# A lifted span is the exception and is allowed to be mechanical - it adds a
# private helper beside the handler and moves no registered symbol.

from fastapi import APIRouter, HTTPException

router = APIRouter()

_ACCOUNTS: dict[str, dict[str, object]] = {}
_WORKSPACES: dict[str, dict[str, object]] = {}
_PROJECTS: dict[str, dict[str, object]] = {}


@router.get("/accounts/{account_id}")
def read_account(account_id: str, expand: str = "") -> dict[str, object]:
    record = _ACCOUNTS.get(account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such account")
    normalized: dict[str, object] = {}
    for key, value in record.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            normalized[key] = value.strip()
        elif isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float)):
            normalized[key] = round(float(value), 2)
        elif isinstance(value, list):
            normalized[key] = sorted(str(item) for item in value)
        else:
            normalized[key] = str(value)
    if expand:
        wanted = {part.strip() for part in expand.split(",") if part.strip()}
        for name in sorted(wanted):
            if name not in normalized:
                normalized[name] = None
    normalized["links"] = {"self": f"/accounts/{account_id}"}
    return normalized


@router.post("/accounts/{account_id}/close")
def close_account(account_id: str, reason: str = "", force: bool = False) -> dict[str, object]:
    record = _ACCOUNTS.get(account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such account")
    if record.get("status") == "closed" and not force:
        raise HTTPException(status_code=409, detail="already closed")
    cleaned = reason.strip()
    if not cleaned and not force:
        raise HTTPException(status_code=422, detail="reason required")
    audit: list[str] = []
    for key, value in sorted(record.items()):
        if key.startswith("_"):
            continue
        if isinstance(value, str) and not value:
            continue
        audit.append(f"{key}={value}")
    record["status"] = "closed"
    record["close_reason"] = cleaned
    record["_audit"] = audit
    return {"account_id": account_id, "status": "closed", "audit_entries": len(audit)}


@router.get("/accounts")
def list_accounts(status: str = "", limit: int = 50) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for key, record in sorted(_ACCOUNTS.items()):
        if status and record.get("status") != status:
            continue
        rows.append({"id": key, "status": record.get("status", "open")})
        if len(rows) >= max(1, limit):
            break
    return {"items": rows, "count": len(rows), "truncated": len(rows) >= max(1, limit)}

@router.get("/workspaces/{workspace_id}")
def read_workspace(workspace_id: str, expand: str = "") -> dict[str, object]:
    record = _WORKSPACES.get(workspace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such workspace")
    normalized: dict[str, object] = {}
    for key, value in record.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            normalized[key] = value.strip()
        elif isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float)):
            normalized[key] = round(float(value), 2)
        elif isinstance(value, list):
            normalized[key] = sorted(str(item) for item in value)
        else:
            normalized[key] = str(value)
    if expand:
        wanted = {part.strip() for part in expand.split(",") if part.strip()}
        for name in sorted(wanted):
            if name not in normalized:
                normalized[name] = None
    normalized["links"] = {"self": f"/workspaces/{workspace_id}"}
    return normalized


@router.post("/workspaces/{workspace_id}/close")
def close_workspace(workspace_id: str, reason: str = "", force: bool = False) -> dict[str, object]:
    record = _WORKSPACES.get(workspace_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such workspace")
    if record.get("status") == "closed" and not force:
        raise HTTPException(status_code=409, detail="already closed")
    cleaned = reason.strip()
    if not cleaned and not force:
        raise HTTPException(status_code=422, detail="reason required")
    audit: list[str] = []
    for key, value in sorted(record.items()):
        if key.startswith("_"):
            continue
        if isinstance(value, str) and not value:
            continue
        audit.append(f"{key}={value}")
    record["status"] = "closed"
    record["close_reason"] = cleaned
    record["_audit"] = audit
    return {"workspace_id": workspace_id, "status": "closed", "audit_entries": len(audit)}


@router.get("/workspaces")
def list_workspaces(status: str = "", limit: int = 50) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for key, record in sorted(_WORKSPACES.items()):
        if status and record.get("status") != status:
            continue
        rows.append({"id": key, "status": record.get("status", "open")})
        if len(rows) >= max(1, limit):
            break
    return {"items": rows, "count": len(rows), "truncated": len(rows) >= max(1, limit)}

@router.get("/projects/{project_id}")
def read_project(project_id: str, expand: str = "") -> dict[str, object]:
    record = _PROJECTS.get(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such project")
    normalized: dict[str, object] = {}
    for key, value in record.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            normalized[key] = value.strip()
        elif isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float)):
            normalized[key] = round(float(value), 2)
        elif isinstance(value, list):
            normalized[key] = sorted(str(item) for item in value)
        else:
            normalized[key] = str(value)
    if expand:
        wanted = {part.strip() for part in expand.split(",") if part.strip()}
        for name in sorted(wanted):
            if name not in normalized:
                normalized[name] = None
    normalized["links"] = {"self": f"/projects/{project_id}"}
    return normalized


@router.post("/projects/{project_id}/close")
def close_project(project_id: str, reason: str = "", force: bool = False) -> dict[str, object]:
    record = _PROJECTS.get(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such project")
    if record.get("status") == "closed" and not force:
        raise HTTPException(status_code=409, detail="already closed")
    cleaned = reason.strip()
    if not cleaned and not force:
        raise HTTPException(status_code=422, detail="reason required")
    audit: list[str] = []
    for key, value in sorted(record.items()):
        if key.startswith("_"):
            continue
        if isinstance(value, str) and not value:
            continue
        audit.append(f"{key}={value}")
    record["status"] = "closed"
    record["close_reason"] = cleaned
    record["_audit"] = audit
    return {"project_id": project_id, "status": "closed", "audit_entries": len(audit)}


@router.get("/projects")
def list_projects(status: str = "", limit: int = 50) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for key, record in sorted(_PROJECTS.items()):
        if status and record.get("status") != status:
            continue
        rows.append({"id": key, "status": record.get("status", "open")})
        if len(rows) >= max(1, limit):
            break
    return {"items": rows, "count": len(rows), "truncated": len(rows) >= max(1, limit)}
