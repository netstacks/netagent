"""Device credentials management routes."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from netagent_core.db import get_db, DeviceCredential
from netagent_core.auth import get_current_user, ALBUser
from netagent_core.utils import audit_log, AuditEventType, encrypt_value, decrypt_value

router = APIRouter()


class CredentialCreate(BaseModel):
    name: str
    description: Optional[str] = None
    device_patterns: List[str]
    username: str
    password: str
    device_type: str = "autodetect"
    port: int = 22
    priority: int = 0
    enabled: bool = True


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    device_patterns: Optional[List[str]] = None
    username: Optional[str] = None
    password: Optional[str] = None
    device_type: Optional[str] = None
    port: Optional[int] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class CredentialResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    device_patterns: List[str]
    username: str  # Will show decrypted
    device_type: str
    port: int
    priority: int
    enabled: bool
    created_by: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class TestConnectionRequest(BaseModel):
    hostname: str
    credential_id: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


@router.get("/credentials", response_model=dict)
async def list_credentials(
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
    enabled: Optional[bool] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
):
    """List all device credentials."""
    query = db.query(DeviceCredential)

    if enabled is not None:
        query = query.filter(DeviceCredential.enabled == enabled)

    total = query.count()
    creds = query.order_by(DeviceCredential.priority.desc(), DeviceCredential.name).offset(offset).limit(limit).all()

    items = []
    for c in creds:
        try:
            username = decrypt_value(c.username_encrypted)
        except Exception:
            username = "<decryption_error>"

        items.append({
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "device_patterns": c.device_patterns,
            "username": username,
            "device_type": c.device_type,
            "port": c.port,
            "priority": c.priority,
            "enabled": c.enabled,
            "created_at": c.created_at.isoformat(),
        })

    return {"items": items, "total": total}


@router.get("/credentials/{cred_id}")
async def get_credential(
    cred_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Get credential by ID."""
    cred = db.query(DeviceCredential).filter(DeviceCredential.id == cred_id).first()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    try:
        username = decrypt_value(cred.username_encrypted)
    except Exception:
        username = "<decryption_error>"

    return {
        "id": cred.id,
        "name": cred.name,
        "description": cred.description,
        "device_patterns": cred.device_patterns,
        "username": username,
        "device_type": cred.device_type,
        "port": cred.port,
        "priority": cred.priority,
        "enabled": cred.enabled,
        "created_at": cred.created_at.isoformat(),
    }


@router.post("/credentials")
async def create_credential(
    data: CredentialCreate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Create new device credentials."""
    cred = DeviceCredential(
        name=data.name,
        description=data.description,
        device_patterns=data.device_patterns,
        username_encrypted=encrypt_value(data.username),
        password_encrypted=encrypt_value(data.password),
        device_type=data.device_type,
        port=data.port,
        priority=data.priority,
        enabled=data.enabled,
        created_by=user.id,
    )

    db.add(cred)
    db.commit()
    db.refresh(cred)

    audit_log(
        db,
        AuditEventType.DEVICE_CREDENTIAL_CREATED,
        user=user,
        resource_type="device_credential",
        resource_id=cred.id,
        resource_name=cred.name,
        action="create",
    )

    return {"id": cred.id, "message": "Credential created"}


@router.put("/credentials/{cred_id}")
async def update_credential(
    cred_id: int,
    data: CredentialUpdate,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Update device credentials."""
    cred = db.query(DeviceCredential).filter(DeviceCredential.id == cred_id).first()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    if data.name is not None:
        cred.name = data.name
    if data.description is not None:
        cred.description = data.description
    if data.device_patterns is not None:
        cred.device_patterns = data.device_patterns
    if data.username is not None:
        cred.username_encrypted = encrypt_value(data.username)
    if data.password is not None:
        cred.password_encrypted = encrypt_value(data.password)
    if data.device_type is not None:
        cred.device_type = data.device_type
    if data.port is not None:
        cred.port = data.port
    if data.priority is not None:
        cred.priority = data.priority
    if data.enabled is not None:
        cred.enabled = data.enabled

    db.commit()

    audit_log(
        db,
        AuditEventType.DEVICE_CREDENTIAL_UPDATED,
        user=user,
        resource_type="device_credential",
        resource_id=cred.id,
        resource_name=cred.name,
        action="update",
    )

    return {"message": "Credential updated"}


@router.delete("/credentials/{cred_id}")
async def delete_credential(
    cred_id: int,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Delete device credentials."""
    cred = db.query(DeviceCredential).filter(DeviceCredential.id == cred_id).first()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    cred_name = cred.name
    db.delete(cred)
    db.commit()

    audit_log(
        db,
        AuditEventType.DEVICE_CREDENTIAL_DELETED,
        user=user,
        resource_type="device_credential",
        resource_id=cred_id,
        resource_name=cred_name,
        action="delete",
    )

    return {"message": "Credential deleted"}


@router.post("/test")
async def test_connection(
    data: TestConnectionRequest,
    db: Session = Depends(get_db),
    user: ALBUser = Depends(get_current_user),
):
    """Test SSH connection to a device."""
    # Get credentials
    username = data.username
    password = data.password

    if data.credential_id:
        cred = db.query(DeviceCredential).filter(DeviceCredential.id == data.credential_id).first()
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")
        username = decrypt_value(cred.username_encrypted)
        password = decrypt_value(cred.password_encrypted)

    if not username or not password:
        raise HTTPException(status_code=400, detail="Credentials required")

    # Test SSH connection with netmiko
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from netmiko import ConnectHandler
    from netmiko.exceptions import (
        NetMikoTimeoutException,
        NetMikoAuthenticationException,
    )

    def _test_ssh_connection():
        """Run SSH test in thread pool (netmiko is blocking)."""
        device = {
            'device_type': 'autodetect',
            'host': data.hostname,
            'username': username,
            'password': password,
            'timeout': 10,
            'auth_timeout': 10,
        }
        try:
            with ConnectHandler(**device) as conn:
                # Try to get basic info
                output = conn.send_command('show version', read_timeout=10)
                device_type = conn.device_type
                return {
                    "success": True,
                    "message": "Connection successful",
                    "hostname": data.hostname,
                    "device_type": device_type,
                    "output_preview": output[:200] if output else None,
                }
        except NetMikoAuthenticationException as e:
            return {
                "success": False,
                "message": f"Authentication failed: {str(e)}",
                "hostname": data.hostname,
            }
        except NetMikoTimeoutException as e:
            return {
                "success": False,
                "message": f"Connection timeout: {str(e)}",
                "hostname": data.hostname,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
                "hostname": data.hostname,
            }

    # Run the blocking netmiko call in a thread pool
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = await loop.run_in_executor(executor, _test_ssh_connection)

    return result
