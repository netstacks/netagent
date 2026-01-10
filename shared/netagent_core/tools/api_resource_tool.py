"""API Resource tool wrapper for agent executor.

Wraps API Resources as tools that can be used by agents to call external REST APIs.
"""

import base64
import json
import logging
import re
from typing import Dict, Any, List, Optional

import httpx

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)


def _sanitize_name(name: str) -> str:
    """Sanitize name for use as tool name."""
    # Convert to lowercase, replace non-alphanumeric with underscore
    sanitized = re.sub(r'[^a-z0-9]', '_', name.lower())
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    return sanitized.strip('_')


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


def _build_combined_schema(
    url_params_schema: Optional[Dict[str, Any]],
    query_params_schema: Optional[Dict[str, Any]],
    request_body_schema: Optional[Dict[str, Any]],
    http_method: str,
) -> Dict[str, Any]:
    """Build combined JSON schema for tool parameters.

    Combines URL params, query params, and request body into a single schema
    with clear namespacing.
    """
    properties = {}
    required = []

    # Add URL params
    if url_params_schema:
        url_props = url_params_schema.get("properties", {})
        for name, prop in url_props.items():
            key = f"url_{name}"
            properties[key] = {
                **prop,
                "description": f"[URL param] {prop.get('description', name)}",
            }
        # URL params from schema required list
        url_required = url_params_schema.get("required", [])
        required.extend([f"url_{r}" for r in url_required])

    # Add query params
    if query_params_schema:
        query_props = query_params_schema.get("properties", {})
        for name, prop in query_props.items():
            key = f"query_{name}"
            properties[key] = {
                **prop,
                "description": f"[Query param] {prop.get('description', name)}",
            }
        query_required = query_params_schema.get("required", [])
        required.extend([f"query_{r}" for r in query_required])

    # Add request body (for POST/PUT/PATCH)
    if http_method in ("POST", "PUT", "PATCH") and request_body_schema:
        body_props = request_body_schema.get("properties", {})
        for name, prop in body_props.items():
            key = f"body_{name}"
            properties[key] = {
                **prop,
                "description": f"[Body field] {prop.get('description', name)}",
            }
        body_required = request_body_schema.get("required", [])
        required.extend([f"body_{r}" for r in body_required])

    return {
        "type": "object",
        "properties": properties,
        "required": required if required else [],
    }


def _extract_params(kwargs: Dict[str, Any]) -> tuple[Dict[str, str], Dict[str, str], Dict[str, Any]]:
    """Extract URL params, query params, and body from combined kwargs."""
    url_params = {}
    query_params = {}
    body = {}

    for key, value in kwargs.items():
        if key.startswith("url_"):
            url_params[key[4:]] = str(value)
        elif key.startswith("query_"):
            query_params[key[6:]] = str(value)
        elif key.startswith("body_"):
            body[key[5:]] = value

    return url_params, query_params, body


class APIResourceToolWrapper:
    """Wraps an API Resource for use with agent executor.

    This class takes an APIResource database record and creates
    a callable wrapper that executes HTTP requests to the configured endpoint.
    """

    def __init__(
        self,
        resource_id: int,
        name: str,
        description: Optional[str],
        url: str,
        http_method: str,
        auth_type: Optional[str],
        auth_config: Optional[Dict[str, Any]],
        request_headers: Optional[Dict[str, str]],
        request_body_schema: Optional[Dict[str, Any]],
        query_params_schema: Optional[Dict[str, Any]],
        url_params_schema: Optional[Dict[str, Any]],
        response_format: str,
        response_path: Optional[str],
        success_codes: List[int],
        risk_level: str,
        requires_approval: bool,
        timeout_seconds: int,
    ):
        """Initialize API Resource tool wrapper."""
        self.resource_id = resource_id
        self.resource_name = name
        self.url = url
        self.http_method = http_method.upper()
        self.auth_type = auth_type
        self.auth_config = auth_config
        self.request_headers = request_headers or {}
        self.request_body_schema = request_body_schema
        self.query_params_schema = query_params_schema
        self.url_params_schema = url_params_schema
        self.response_format = response_format
        self.response_path = response_path
        self.success_codes = success_codes or [200, 201, 204]
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.timeout_seconds = timeout_seconds

        # Build tool name: api_{sanitized_resource_name}
        sanitized = _sanitize_name(name)
        self.tool_name = f"api_{sanitized}"

        # Build description with context
        self.description = description or f"Call the {name} API"

        # Build combined parameter schema
        self.parameters = _build_combined_schema(
            url_params_schema,
            query_params_schema,
            request_body_schema,
            self.http_method,
        )

    async def execute(self, **kwargs) -> str:
        """Execute the API call.

        Args:
            **kwargs: Tool arguments (prefixed with url_, query_, body_)

        Returns:
            API response as string
        """
        try:
            # Extract params from kwargs
            url_params, query_params, body = _extract_params(kwargs)

            # Build final URL with path params
            final_url = _substitute_url_params(self.url, url_params)

            # Build headers
            headers = _build_auth_headers(self.auth_type, self.auth_config)
            headers.update(self.request_headers)

            # Set content type for POST/PUT/PATCH
            if self.http_method in ("POST", "PUT", "PATCH") and body:
                headers.setdefault("Content-Type", "application/json")

            logger.info(f"Calling API resource {self.tool_name}: {self.http_method} {final_url}")

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method=self.http_method,
                    url=final_url,
                    headers=headers,
                    params=query_params if query_params else None,
                    json=body if body and self.http_method in ("POST", "PUT", "PATCH") else None,
                )

                # Check if response is successful
                if response.status_code not in self.success_codes:
                    error_text = response.text[:500] if response.text else "No response body"
                    return f"API Error: HTTP {response.status_code} - {error_text}"

                # Parse response based on format
                if self.response_format == "json":
                    try:
                        response_data = response.json()

                        # Extract specific path if configured
                        if self.response_path:
                            response_data = self._extract_json_path(response_data, self.response_path)

                        # Format for display
                        if isinstance(response_data, (dict, list)):
                            return json.dumps(response_data, indent=2)
                        return str(response_data)

                    except json.JSONDecodeError:
                        return response.text

                elif self.response_format == "text":
                    return response.text

                else:
                    return f"[Binary response: {len(response.content)} bytes]"

        except httpx.TimeoutException:
            return f"Error: Request timed out after {self.timeout_seconds} seconds"
        except httpx.ConnectError as e:
            return f"Error: Connection failed - {str(e)}"
        except Exception as e:
            logger.error(f"API resource tool execution failed: {e}")
            return f"Error executing API call: {str(e)}"

    def _extract_json_path(self, data: Any, path: str) -> Any:
        """Extract data using simple JSONPath-like notation.

        Supports simple dot notation like "data.items" or "result.value"
        """
        if not path:
            return data

        parts = path.split(".")
        current = data

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list):
                try:
                    index = int(part)
                    current = current[index]
                except (ValueError, IndexError):
                    return data  # Return original if path doesn't work
            else:
                return data  # Return original if path doesn't work

        return current


def create_api_resource_tool(
    resource_id: int,
    name: str,
    description: Optional[str],
    url: str,
    http_method: str,
    auth_type: Optional[str],
    auth_config: Optional[Dict[str, Any]],
    request_headers: Optional[Dict[str, str]],
    request_body_schema: Optional[Dict[str, Any]],
    query_params_schema: Optional[Dict[str, Any]],
    url_params_schema: Optional[Dict[str, Any]],
    response_format: str,
    response_path: Optional[str],
    success_codes: List[int],
    risk_level: str,
    requires_approval: bool,
    timeout_seconds: int,
) -> ToolDefinition:
    """Create a ToolDefinition from an API Resource.

    Args:
        All API resource configuration fields

    Returns:
        ToolDefinition for use with agent executor
    """
    wrapper = APIResourceToolWrapper(
        resource_id=resource_id,
        name=name,
        description=description,
        url=url,
        http_method=http_method,
        auth_type=auth_type,
        auth_config=auth_config,
        request_headers=request_headers,
        request_body_schema=request_body_schema,
        query_params_schema=query_params_schema,
        url_params_schema=url_params_schema,
        response_format=response_format,
        response_path=response_path,
        success_codes=success_codes,
        risk_level=risk_level,
        requires_approval=requires_approval,
        timeout_seconds=timeout_seconds,
    )

    return ToolDefinition(
        name=wrapper.tool_name,
        description=wrapper.description,
        parameters=wrapper.parameters,
        handler=wrapper.execute,
        requires_approval=wrapper.requires_approval,
        risk_level=wrapper.risk_level,
    )


async def load_api_resources_for_agent(
    api_resource_ids: List[int],
    db_session_factory,
) -> List[ToolDefinition]:
    """Load all API resource tools for an agent.

    Args:
        api_resource_ids: IDs of API resources to load
        db_session_factory: Factory for database sessions

    Returns:
        List of ToolDefinitions for all API resources
    """
    if not api_resource_ids:
        return []

    tools = []

    with db_session_factory() as db:
        from ..db import APIResource
        from ..utils.encryption import decrypt_value

        resources = db.query(APIResource).filter(
            APIResource.id.in_(api_resource_ids),
            APIResource.enabled == True,
        ).all()

        for resource in resources:
            try:
                # Decrypt auth config
                auth_config = None
                if resource.auth_config_encrypted:
                    try:
                        auth_config = json.loads(decrypt_value(resource.auth_config_encrypted))
                    except Exception as e:
                        logger.warning(f"Failed to decrypt auth config for {resource.name}: {e}")

                # Create tool definition
                tool = create_api_resource_tool(
                    resource_id=resource.id,
                    name=resource.name,
                    description=resource.description,
                    url=resource.url,
                    http_method=resource.http_method,
                    auth_type=resource.auth_type,
                    auth_config=auth_config,
                    request_headers=resource.request_headers,
                    request_body_schema=resource.request_body_schema,
                    query_params_schema=resource.query_params_schema,
                    url_params_schema=resource.url_params_schema,
                    response_format=resource.response_format,
                    response_path=resource.response_path,
                    success_codes=resource.success_codes or [200, 201, 204],
                    risk_level=resource.risk_level,
                    requires_approval=resource.requires_approval,
                    timeout_seconds=resource.timeout_seconds,
                )

                tools.append(tool)
                logger.info(f"Loaded API resource tool: {tool.name}")

            except Exception as e:
                logger.error(f"Failed to load API resource {resource.name}: {e}")
                # Continue with other resources

    return tools
