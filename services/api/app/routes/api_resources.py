"""API Resources management routes.

API Resources allow users to define custom REST API endpoints that agents can call as tools.
Similar to MCP servers but with direct HTTP calls without protocol overhead.
"""

import base64
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from netagent_core.db import get_db, APIResource
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType, encrypt_value, decrypt_value

router = APIRouter()


# =============================================================================
# Pydantic Models
# =============================================================================


class APIResourceCreate(BaseModel):
    """Request model for creating an API resource."""
    name: str
    description: Optional[str] = None
    url: str
    http_method: str = "GET"
    auth_type: Optional[str] = None  # none, bearer, basic, api_key, custom_headers
    auth_config: Optional[Dict[str, Any]] = None  # Structure depends on auth_type
    request_headers: Optional[Dict[str, str]] = None
    request_body_schema: Optional[Dict[str, Any]] = None
    query_params_schema: Optional[Dict[str, Any]] = None
    url_params_schema: Optional[Dict[str, Any]] = None
    response_format: str = "json"
    response_path: Optional[str] = None
    success_codes: Optional[List[int]] = None
    risk_level: str = "low"
    requires_approval: bool = False
    timeout_seconds: int = 30
    enabled: bool = True


class APIResourceUpdate(BaseModel):
    """Request model for updating an API resource."""
    name: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    http_method: Optional[str] = None
    auth_type: Optional[str] = None
    auth_config: Optional[Dict[str, Any]] = None
    request_headers: Optional[Dict[str, str]] = None
    request_body_schema: Optional[Dict[str, Any]] = None
    query_params_schema: Optional[Dict[str, Any]] = None
    url_params_schema: Optional[Dict[str, Any]] = None
    response_format: Optional[str] = None
    response_path: Optional[str] = None
    success_codes: Optional[List[int]] = None
    risk_level: Optional[str] = None
    requires_approval: Optional[bool] = None
    timeout_seconds: Optional[int] = None
    enabled: Optional[bool] = None


class APIResourceResponse(BaseModel):
    """Response model for API resource."""
    id: int
    name: str
    description: Optional[str]
    url: str
    http_method: str
    auth_type: Optional[str]
    request_headers: Optional[Dict[str, str]]
    request_body_schema: Optional[Dict[str, Any]]
    query_params_schema: Optional[Dict[str, Any]]
    url_params_schema: Optional[Dict[str, Any]]
    response_format: str
    response_path: Optional[str]
    success_codes: Optional[List[int]]
    risk_level: str
    requires_approval: bool
    timeout_seconds: int
    health_status: str
    last_tested_at: Optional[datetime]
    enabled: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class APIResourceTestRequest(BaseModel):
    """Request model for testing an API resource (without saving)."""
    url: str
    http_method: str = "GET"
    auth_type: Optional[str] = None
    auth_config: Optional[Dict[str, Any]] = None
    request_headers: Optional[Dict[str, str]] = None
    request_body: Optional[Dict[str, Any]] = None
    query_params: Optional[Dict[str, str]] = None
    url_params: Optional[Dict[str, str]] = None
    timeout_seconds: int = 30


class APIResourceCallRequest(BaseModel):
    """Request model for calling a saved API resource."""
    request_body: Optional[Dict[str, Any]] = None
    query_params: Optional[Dict[str, str]] = None
    url_params: Optional[Dict[str, str]] = None


# =============================================================================
# Helper Functions
# =============================================================================


def _build_auth_headers(auth_type: Optional[str], auth_config: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Build authentication headers based on auth type and config."""
    headers = {}

    if not auth_type or auth_type == "none":
        return headers

    if not auth_config:
        return headers

    if auth_type == "bearer":
        token = auth_config.get("token", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "basic":
        username = auth_config.get("username", "")
        password = auth_config.get("password", "")
        if username:
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {credentials}"

    elif auth_type == "api_key":
        header_name = auth_config.get("header_name", "X-API-Key")
        header_value = auth_config.get("header_value", "")
        if header_value:
            headers[header_name] = header_value

    elif auth_type == "custom_headers":
        custom_headers = auth_config.get("headers", {})
        headers.update(custom_headers)

    return headers


def _substitute_url_params(url: str, params: Optional[Dict[str, str]]) -> str:
    """Substitute {param} placeholders in URL with actual values."""
    if not params:
        return url

    for key, value in params.items():
        url = url.replace(f"{{{key}}}", str(value))

    return url


async def _execute_api_call(
    url: str,
    http_method: str,
    auth_type: Optional[str] = None,
    auth_config: Optional[Dict[str, Any]] = None,
    request_headers: Optional[Dict[str, str]] = None,
    request_body: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, str]] = None,
    url_params: Optional[Dict[str, str]] = None,
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """Execute an API call and return the result."""
    # Build final URL with path params
    final_url = _substitute_url_params(url, url_params)

    # Build headers
    headers = _build_auth_headers(auth_type, auth_config)
    if request_headers:
        headers.update(request_headers)

    # Set content type for POST/PUT/PATCH
    if http_method in ("POST", "PUT", "PATCH") and request_body:
        headers.setdefault("Content-Type", "application/json")

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=http_method,
                url=final_url,
                headers=headers,
                params=query_params,
                json=request_body if http_method in ("POST", "PUT", "PATCH") else None,
            )

            # Try to parse response as JSON
            try:
                response_data = response.json()
            except Exception:
                response_data = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response_data,
                "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timed out")
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Connection failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")


# =============================================================================
# CRUD Endpoints
# =============================================================================


@router.get("", response_model=dict)
async def list_api_resources(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all API resources."""
    query = db.query(APIResource)

    if enabled is not None:
        query = query.filter(APIResource.enabled == enabled)

    total = query.count()
    resources = query.order_by(APIResource.name).offset(offset).limit(limit).all()

    return {
        "items": [APIResourceResponse.model_validate(r) for r in resources],
        "total": total,
    }


@router.get("/{resource_id}", response_model=APIResourceResponse)
async def get_api_resource(
    resource_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get API resource by ID."""
    resource = db.query(APIResource).filter(APIResource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="API resource not found")

    return APIResourceResponse.model_validate(resource)


@router.post("")
async def create_api_resource(
    data: APIResourceCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create a new API resource."""
    # Encrypt auth config if provided
    auth_config_encrypted = None
    if data.auth_config:
        auth_config_encrypted = encrypt_value(json.dumps(data.auth_config))

    resource = APIResource(
        name=data.name,
        description=data.description,
        url=data.url,
        http_method=data.http_method.upper(),
        auth_type=data.auth_type,
        auth_config_encrypted=auth_config_encrypted,
        request_headers=data.request_headers or {},
        request_body_schema=data.request_body_schema,
        query_params_schema=data.query_params_schema,
        url_params_schema=data.url_params_schema,
        response_format=data.response_format,
        response_path=data.response_path,
        success_codes=data.success_codes or [200, 201, 204],
        risk_level=data.risk_level,
        requires_approval=data.requires_approval,
        timeout_seconds=data.timeout_seconds,
        enabled=data.enabled,
        created_by=user.id if user else None,
    )

    db.add(resource)
    db.commit()
    db.refresh(resource)

    audit_log(
        db,
        AuditEventType.API_RESOURCE_CREATED,
        user=user,
        resource_type="api_resource",
        resource_id=resource.id,
        resource_name=resource.name,
        action="create",
    )

    return {"id": resource.id, "message": "API resource created"}


@router.put("/{resource_id}")
async def update_api_resource(
    resource_id: int,
    data: APIResourceUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update an API resource."""
    resource = db.query(APIResource).filter(APIResource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="API resource not found")

    # Update fields if provided
    if data.name is not None:
        resource.name = data.name
    if data.description is not None:
        resource.description = data.description
    if data.url is not None:
        resource.url = data.url
    if data.http_method is not None:
        resource.http_method = data.http_method.upper()
    if data.auth_type is not None:
        resource.auth_type = data.auth_type
    if data.auth_config is not None:
        resource.auth_config_encrypted = encrypt_value(json.dumps(data.auth_config))
    if data.request_headers is not None:
        resource.request_headers = data.request_headers
    if data.request_body_schema is not None:
        resource.request_body_schema = data.request_body_schema
    if data.query_params_schema is not None:
        resource.query_params_schema = data.query_params_schema
    if data.url_params_schema is not None:
        resource.url_params_schema = data.url_params_schema
    if data.response_format is not None:
        resource.response_format = data.response_format
    if data.response_path is not None:
        resource.response_path = data.response_path
    if data.success_codes is not None:
        resource.success_codes = data.success_codes
    if data.risk_level is not None:
        resource.risk_level = data.risk_level
    if data.requires_approval is not None:
        resource.requires_approval = data.requires_approval
    if data.timeout_seconds is not None:
        resource.timeout_seconds = data.timeout_seconds
    if data.enabled is not None:
        resource.enabled = data.enabled

    resource.updated_at = datetime.utcnow()
    db.commit()

    audit_log(
        db,
        AuditEventType.API_RESOURCE_UPDATED,
        user=user,
        resource_type="api_resource",
        resource_id=resource.id,
        resource_name=resource.name,
        action="update",
    )

    return {"message": "API resource updated"}


@router.delete("/{resource_id}")
async def delete_api_resource(
    resource_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete an API resource."""
    resource = db.query(APIResource).filter(APIResource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="API resource not found")

    resource_name = resource.name
    db.delete(resource)
    db.commit()

    audit_log(
        db,
        AuditEventType.API_RESOURCE_DELETED,
        user=user,
        resource_type="api_resource",
        resource_id=resource_id,
        resource_name=resource_name,
        action="delete",
    )

    return {"message": "API resource deleted"}


# =============================================================================
# Test and Health Endpoints
# =============================================================================


@router.post("/test")
async def test_api_resource(
    data: APIResourceTestRequest,
    user: ALBUser = Depends(get_current_user),
):
    """Test an API resource configuration without saving.

    Useful for validating connectivity and auth before creating a resource.
    """
    result = await _execute_api_call(
        url=data.url,
        http_method=data.http_method.upper(),
        auth_type=data.auth_type,
        auth_config=data.auth_config,
        request_headers=data.request_headers,
        request_body=data.request_body,
        query_params=data.query_params,
        url_params=data.url_params,
        timeout_seconds=data.timeout_seconds,
    )

    return {
        "status": "success" if result["status_code"] < 400 else "error",
        **result,
    }


@router.post("/{resource_id}/test")
async def test_existing_resource(
    resource_id: int,
    data: Optional[APIResourceCallRequest] = None,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Test an existing API resource."""
    resource = db.query(APIResource).filter(APIResource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="API resource not found")

    # Decrypt auth config
    auth_config = None
    if resource.auth_config_encrypted:
        try:
            auth_config = json.loads(decrypt_value(resource.auth_config_encrypted))
        except Exception:
            pass

    # Use provided params or empty
    call_data = data or APIResourceCallRequest()

    result = await _execute_api_call(
        url=resource.url,
        http_method=resource.http_method,
        auth_type=resource.auth_type,
        auth_config=auth_config,
        request_headers=resource.request_headers,
        request_body=call_data.request_body,
        query_params=call_data.query_params,
        url_params=call_data.url_params,
        timeout_seconds=resource.timeout_seconds,
    )

    # Update health status
    success_codes = resource.success_codes or [200, 201, 204]
    is_healthy = result["status_code"] in success_codes
    resource.health_status = "healthy" if is_healthy else "unhealthy"
    resource.last_tested_at = datetime.utcnow()
    db.commit()

    return {
        "status": "success" if is_healthy else "error",
        **result,
    }


@router.post("/{resource_id}/health")
async def check_health(
    resource_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Check API resource health with a simple request."""
    resource = db.query(APIResource).filter(APIResource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=404, detail="API resource not found")

    # Decrypt auth config
    auth_config = None
    if resource.auth_config_encrypted:
        try:
            auth_config = json.loads(decrypt_value(resource.auth_config_encrypted))
        except Exception:
            pass

    try:
        result = await _execute_api_call(
            url=resource.url,
            http_method=resource.http_method,
            auth_type=resource.auth_type,
            auth_config=auth_config,
            request_headers=resource.request_headers,
            timeout_seconds=min(resource.timeout_seconds, 10),  # Cap at 10s for health
        )

        success_codes = resource.success_codes or [200, 201, 204]
        is_healthy = result["status_code"] in success_codes

        resource.health_status = "healthy" if is_healthy else "unhealthy"
        resource.last_tested_at = datetime.utcnow()
        db.commit()

        return {
            "healthy": is_healthy,
            "status": resource.health_status,
            "status_code": result["status_code"],
            "elapsed_ms": result["elapsed_ms"],
        }

    except Exception as e:
        resource.health_status = "unhealthy"
        resource.last_tested_at = datetime.utcnow()
        db.commit()

        return {
            "healthy": False,
            "status": "unhealthy",
            "error": str(e),
        }
