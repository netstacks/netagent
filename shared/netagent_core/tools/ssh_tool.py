"""SSH command tool for network device access.

Provides read-only SSH access to network devices using stored credentials.
Supports command filtering to prevent configuration changes.
"""

import asyncio
import fnmatch
import logging
import re
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from ..llm.agent_executor import ToolDefinition

logger = logging.getLogger(__name__)

# Maximum output size to prevent LLM context overflow (in characters)
MAX_OUTPUT_SIZE = 50000  # ~50KB, roughly 12-15K tokens

# Mapping from NetBox manufacturer/platform names to Netmiko device types
# This is critical for proper prompt detection and command handling
MANUFACTURER_TO_NETMIKO = {
    # Juniper
    "juniper": "juniper_junos",
    "junos": "juniper_junos",
    # Cisco
    "cisco": "cisco_ios",  # Default for Cisco, may need refinement
    "cisco ios": "cisco_ios",
    "cisco ios-xe": "cisco_xe",
    "cisco ios-xr": "cisco_xr",
    "cisco nx-os": "cisco_nxos",
    "cisco nxos": "cisco_nxos",
    "cisco asa": "cisco_asa",
    # Arista
    "arista": "arista_eos",
    "arista eos": "arista_eos",
    # Linux/Unix
    "linux": "linux",
    "ubuntu": "linux",
    "centos": "linux",
    "redhat": "linux",
    # Others
    "paloalto": "paloalto_panos",
    "palo alto": "paloalto_panos",
    "fortinet": "fortinet",
    "f5": "f5_tmsh",
    "dell": "dell_force10",
    "hp": "hp_procurve",
    "huawei": "huawei",
    "mikrotik": "mikrotik_routeros",
}

# Mapping from NetBox device type model patterns to Netmiko device types
DEVICE_MODEL_TO_NETMIKO = {
    # Juniper
    "mx": "juniper_junos",
    "ex": "juniper_junos",
    "qfx": "juniper_junos",
    "srx": "juniper_junos",
    "ptx": "juniper_junos",
    "acx": "juniper_junos",
    # Cisco
    "nexus": "cisco_nxos",
    "n9k": "cisco_nxos",
    "n7k": "cisco_nxos",
    "n5k": "cisco_nxos",
    "asr": "cisco_xr",  # ASR 9000 series uses IOS-XR
    "ncs": "cisco_xr",
    "catalyst": "cisco_ios",
    "isr": "cisco_ios",
    # Arista
    "dcs": "arista_eos",
}

# Dangerous command patterns that should be blocked
BLOCKED_PATTERNS = [
    r"^conf",  # configure, conf t
    r"^write",  # write mem
    r"^copy",  # copy running-config
    r"^delete",
    r"^erase",
    r"^format",
    r"^reload",
    r"^reboot",
    r"^shutdown$",  # interface shutdown is ok in show commands
    r"^no\s+",  # no commands
    r"^clear\s+(?!counters)",  # clear except clear counters (which is read-only info)
    r"^debug",
    r"^undebug",
    r"^terminal\s+(?!length|width)",  # terminal except display settings
    r"^request\s+system",
    r"^set\s+",
    r"^edit\s+",
    r"^commit",
    r"^rollback",
]

# Safe command patterns (show, display, ping, traceroute)
SAFE_PATTERNS = [
    r"^show\s+",
    r"^display\s+",
    r"^get\s+",
    r"^ping\s+",
    r"^traceroute\s+",
    r"^tracert\s+",
    r"^who$",
    r"^whoami$",
    r"^uptime$",
    r"^hostname$",
    r"^uname\s+",
    r"^cat\s+/proc/",  # Linux system info
    r"^ip\s+(addr|route|link|neigh)",  # Linux networking
    r"^netstat\s+",
    r"^ss\s+",
    r"^arp\s+",
    r"^route\s+",
    r"^ifconfig\s*",
]


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check if a command is safe to execute.

    Args:
        command: The command to check

    Returns:
        Tuple of (is_safe, reason)
    """
    command = command.strip().lower()

    # Check blocked patterns first
    for pattern in BLOCKED_PATTERNS:
        if re.match(pattern, command, re.IGNORECASE):
            return False, f"Command matches blocked pattern: {pattern}"

    # Check if command matches safe patterns
    for pattern in SAFE_PATTERNS:
        if re.match(pattern, command, re.IGNORECASE):
            return True, "Command matches safe pattern"

    # Default: block unknown commands
    return False, "Command does not match any known safe pattern"


def is_device_allowed(
    hostname: str,
    allowed_patterns: List[str],
) -> bool:
    """Check if a device hostname matches allowed patterns.

    Args:
        hostname: Device hostname to check
        allowed_patterns: List of glob patterns (e.g., ["router-*", "switch-nyc-*"])

    Returns:
        True if device matches any allowed pattern
    """
    hostname = hostname.lower()
    for pattern in allowed_patterns:
        if fnmatch.fnmatch(hostname, pattern.lower()):
            return True
    return False


def get_netmiko_device_type(manufacturer: str = None, model: str = None, platform: str = None) -> Optional[str]:
    """Determine the Netmiko device type from NetBox device info.

    Args:
        manufacturer: Device manufacturer name (e.g., "Juniper", "Cisco")
        model: Device model/type display name (e.g., "MX960", "Nexus 9000")
        platform: Platform slug if available (e.g., "junos", "ios-xe")

    Returns:
        Netmiko device type string or None if not determined
    """
    # Try platform first (most specific)
    if platform:
        platform_lower = platform.lower()
        if platform_lower in MANUFACTURER_TO_NETMIKO:
            return MANUFACTURER_TO_NETMIKO[platform_lower]

    # Try model prefix matching
    if model:
        model_lower = model.lower()
        for prefix, device_type in DEVICE_MODEL_TO_NETMIKO.items():
            if model_lower.startswith(prefix):
                return device_type

    # Try manufacturer (least specific but reliable fallback)
    if manufacturer:
        manufacturer_lower = manufacturer.lower()
        if manufacturer_lower in MANUFACTURER_TO_NETMIKO:
            return MANUFACTURER_TO_NETMIKO[manufacturer_lower]

    return None


class SSHCommandTool:
    """Tool for executing read-only SSH commands on network devices."""

    name = "ssh_command"
    description = """Execute a read-only command on a network device via SSH.
Only show/display commands and basic diagnostics (ping, traceroute) are allowed.
Configuration commands are blocked for safety."""

    parameters = {
        "type": "object",
        "properties": {
            "hostname": {
                "type": "string",
                "description": "Hostname or IP address of the network device",
            },
            "command": {
                "type": "string",
                "description": "Command to execute (must be read-only, e.g., show commands)",
            },
        },
        "required": ["hostname", "command"],
    }

    requires_approval = False
    risk_level = "low"

    def __init__(
        self,
        allowed_device_patterns: List[str] = None,
        db_session_factory=None,
        encryption_key: str = None,
        mcp_client=None,
    ):
        """Initialize SSH tool.

        Args:
            allowed_device_patterns: Glob patterns for allowed devices
            db_session_factory: Factory for database sessions (to get credentials)
            encryption_key: Key for decrypting stored credentials
            mcp_client: Optional MCP client for NetBox lookups
        """
        self.allowed_device_patterns = allowed_device_patterns or ["*"]
        self.db_session_factory = db_session_factory
        self.encryption_key = encryption_key
        self.mcp_client = mcp_client

    async def _lookup_device_type_from_netbox(self, hostname: str) -> Optional[str]:
        """Look up device type from NetBox using MCP or direct API.

        Args:
            hostname: Device hostname or IP address to look up

        Returns:
            Netmiko device type string or None
        """
        # Try to get NetBox MCP server from database
        if not self.db_session_factory:
            return None

        try:
            from ..db import MCPServer
            from ..mcp import MCPClient
            from ..utils.encryption import decrypt_value
            import ipaddress
            import socket

            # Check if hostname is actually an IP address
            is_ip = False
            resolved_hostname = None
            try:
                ipaddress.ip_address(hostname)
                is_ip = True
                # Try reverse DNS lookup since all devices have DNS records
                try:
                    resolved_hostname, _, _ = socket.gethostbyaddr(hostname)
                    logger.info(f"Resolved IP {hostname} to hostname: {resolved_hostname}")
                except socket.herror as e:
                    logger.debug(f"Reverse DNS lookup failed for {hostname}: {e}")
            except ValueError:
                pass

            # Use resolved hostname if available, otherwise use original
            lookup_hostname = resolved_hostname if resolved_hostname else hostname

            with self.db_session_factory() as db:
                # Find NetBox MCP server
                netbox_server = db.query(MCPServer).filter(
                    MCPServer.name.ilike("%netbox%"),
                    MCPServer.enabled == True,
                ).first()

                if not netbox_server:
                    logger.debug("No NetBox MCP server configured")
                    return None

                # Get auth token
                auth_token = None
                if netbox_server.auth_config_encrypted:
                    auth_token = decrypt_value(netbox_server.auth_config_encrypted)

                # Create MCP client
                client = MCPClient(
                    base_url=netbox_server.base_url,
                    auth_type=netbox_server.auth_type,
                    auth_token=auth_token,
                )

                # Query NetBox for device
                # If we have a resolved hostname from DNS, use it for lookup
                # Otherwise if it's an IP, try to search by primary IP
                if is_ip and not resolved_hostname:
                    # Search by primary IP address as fallback when no DNS resolution
                    logger.debug(f"Looking up device by IP address: {hostname}")
                    result = await client.call_tool("netbox_get_objects", {
                        "object_type": "dcim.device",
                        "filters": {"primary_ip4__address": hostname},
                        "fields": ["name", "device_type", "platform"],
                        "limit": 5,
                    })
                else:
                    # Search by hostname - extract just the hostname part (before first dot if FQDN)
                    short_hostname = lookup_hostname.split('.')[0]
                    logger.debug(f"Looking up device by hostname: {short_hostname}")
                    result = await client.call_tool("netbox_get_objects", {
                        "object_type": "dcim.device",
                        "filters": {"name__ic": short_hostname},
                        "fields": ["name", "device_type", "platform"],
                        "limit": 5,
                    })

                if not result:
                    return None

                # Parse the result
                content = result.get("content", [])
                if not content:
                    return None

                # Get text content from MCP response
                text_content = None
                for item in content:
                    if item.get("type") == "text":
                        text_content = item.get("text", "")
                        break

                if not text_content:
                    return None

                import json
                try:
                    data = json.loads(text_content)
                except json.JSONDecodeError:
                    return None

                results = data.get("results", [])
                if not results:
                    return None

                # Find exact or closest match
                # Use lookup_hostname (which may be DNS-resolved from IP) for matching
                device = None
                for r in results:
                    if r.get("name", "").lower() == lookup_hostname.lower():
                        device = r
                        break
                    if r.get("name", "").lower().startswith(short_hostname.lower()):
                        device = r
                        break

                if not device:
                    device = results[0]  # Use first result as fallback

                # Extract manufacturer and model info
                device_type_info = device.get("device_type", {})
                manufacturer = None
                model = None
                platform = None

                if isinstance(device_type_info, dict):
                    model = device_type_info.get("display") or device_type_info.get("model")
                    manufacturer_info = device_type_info.get("manufacturer", {})
                    if isinstance(manufacturer_info, dict):
                        manufacturer = manufacturer_info.get("name") or manufacturer_info.get("display")

                platform_info = device.get("platform", {})
                if isinstance(platform_info, dict):
                    platform = platform_info.get("slug") or platform_info.get("name")

                # Determine netmiko device type
                netmiko_type = get_netmiko_device_type(
                    manufacturer=manufacturer,
                    model=model,
                    platform=platform
                )

                if netmiko_type:
                    logger.info(f"Determined device type for {hostname}: {netmiko_type} "
                               f"(manufacturer={manufacturer}, model={model}, platform={platform})")

                return netmiko_type

        except Exception as e:
            logger.warning(f"Failed to look up device type from NetBox for {hostname}: {e}")
            return None

    async def execute(self, hostname: str, command: str) -> str:
        """Execute SSH command on device.

        Args:
            hostname: Device hostname or IP
            command: Command to execute

        Returns:
            Command output or error message
        """
        # Validate device is allowed
        if not is_device_allowed(hostname, self.allowed_device_patterns):
            return f"Error: Device '{hostname}' is not in the allowed device list"

        # Validate command is safe
        is_safe, reason = is_command_safe(command)
        if not is_safe:
            return f"Error: Command blocked - {reason}. Only read-only commands (show, display, ping, traceroute) are allowed."

        # Get credentials for device
        credentials = await self._get_credentials(hostname)
        if not credentials:
            return f"Error: No credentials found for device '{hostname}'"

        # Determine device type - prefer NetBox lookup, then credentials, then autodetect
        device_type = credentials.get("device_type", "autodetect")

        if device_type == "autodetect":
            # Try to look up device type from NetBox
            netbox_device_type = await self._lookup_device_type_from_netbox(hostname)
            if netbox_device_type:
                device_type = netbox_device_type
                logger.info(f"Using device type from NetBox for {hostname}: {device_type}")
            else:
                logger.warning(f"Could not determine device type for {hostname}, using autodetect")

        # Execute command
        try:
            output = await self._ssh_execute(
                hostname=hostname,
                command=command,
                username=credentials["username"],
                password=credentials["password"],
                device_type=device_type,
                port=credentials.get("port", 22),
            )

            # Truncate output if too large to prevent LLM context overflow
            if len(output) > MAX_OUTPUT_SIZE:
                truncated_lines = output[:MAX_OUTPUT_SIZE].rsplit('\n', 1)[0]
                original_lines = output.count('\n')
                truncated_line_count = truncated_lines.count('\n')
                output = (
                    f"{truncated_lines}\n\n"
                    f"... OUTPUT TRUNCATED (showing {truncated_line_count} of {original_lines} lines, "
                    f"{len(truncated_lines):,} of {len(output):,} characters) ...\n"
                    f"Use more specific filters or grep to reduce output size."
                )
                logger.warning(f"SSH output truncated from {len(output):,} to {len(truncated_lines):,} chars for {hostname}")

            return output
        except Exception as e:
            logger.error(f"SSH execution error: {e}")
            return f"Error executing command: {str(e)}"

    async def _get_credentials(self, hostname: str) -> Optional[Dict[str, Any]]:
        """Get credentials for a device from database.

        Args:
            hostname: Device hostname

        Returns:
            Credentials dict or None
        """
        if not self.db_session_factory:
            # Return mock credentials for testing
            return {
                "username": "admin",
                "password": "admin",
                "device_type": "autodetect",
                "port": 22,
            }

        try:
            from ..db import DeviceCredential
            from ..utils.encryption import decrypt_value

            with self.db_session_factory() as db:
                # Find matching credentials (highest priority first)
                credentials = (
                    db.query(DeviceCredential)
                    .filter(DeviceCredential.enabled == True)
                    .order_by(DeviceCredential.priority.desc())
                    .all()
                )

                for cred in credentials:
                    # Check if hostname matches any pattern
                    for pattern in cred.device_patterns:
                        # Support both glob patterns (fnmatch) and regex patterns
                        matched = False
                        # First try fnmatch (glob-style: *, ?, [])
                        if fnmatch.fnmatch(hostname.lower(), pattern.lower()):
                            matched = True
                        # If not matched and pattern looks like regex, try regex
                        elif any(c in pattern for c in ['.', '^', '$', '+', '\\', '|', '(', ')']):
                            try:
                                import re
                                if re.match(pattern, hostname, re.IGNORECASE):
                                    matched = True
                            except re.error:
                                pass  # Invalid regex, ignore
                        if matched:
                            return {
                                "username": decrypt_value(cred.username_encrypted),
                                "password": decrypt_value(cred.password_encrypted),
                                "device_type": cred.device_type,
                                "port": cred.port,
                            }

                return None
        except Exception as e:
            logger.error(f"Error getting credentials: {e}")
            return None

    async def _ssh_execute(
        self,
        hostname: str,
        command: str,
        username: str,
        password: str,
        device_type: str = "autodetect",
        port: int = 22,
    ) -> str:
        """Execute SSH command using netmiko.

        Args:
            hostname: Device hostname/IP
            command: Command to run
            username: SSH username
            password: SSH password
            device_type: Netmiko device type
            port: SSH port

        Returns:
            Command output
        """
        try:
            import netmiko
            from netmiko import ConnectHandler
            from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
        except ImportError:
            return "Error: netmiko library not installed"

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()

        def _connect_and_run():
            device = {
                "device_type": device_type,
                "host": hostname,
                "username": username,
                "password": password,
                "port": port,
                "timeout": 30,
                "conn_timeout": 30,
                "auth_timeout": 30,
                "banner_timeout": 30,
            }

            # Add device-specific settings
            if device_type.startswith("juniper"):
                # Juniper devices need these settings for reliable operation
                device["global_delay_factor"] = 2
                device["fast_cli"] = False

            try:
                with ConnectHandler(**device) as conn:
                    # For Juniper, ensure CLI mode and disable pagination
                    if device_type.startswith("juniper"):
                        try:
                            conn.send_command("set cli screen-length 0", read_timeout=10)
                            conn.send_command("set cli screen-width 0", read_timeout=10)
                        except Exception:
                            pass  # Continue even if these fail

                    output = conn.send_command(
                        command,
                        read_timeout=90,
                        strip_command=True,
                        strip_prompt=True,
                    )
                    return output
            except NetmikoTimeoutException:
                return f"Error: Connection to {hostname} timed out"
            except NetmikoAuthenticationException:
                return f"Error: Authentication failed for {hostname}"
            except Exception as e:
                return f"Error: {str(e)}"

        try:
            output = await loop.run_in_executor(None, _connect_and_run)
            return output
        except Exception as e:
            return f"Error: {str(e)}"


def create_ssh_tool(
    allowed_device_patterns: List[str] = None,
    db_session_factory=None,
    encryption_key: str = None,
) -> ToolDefinition:
    """Create SSH tool definition for agent executor.

    Args:
        allowed_device_patterns: Device patterns this agent can access
        db_session_factory: Database session factory
        encryption_key: Encryption key for credentials

    Returns:
        ToolDefinition for the SSH tool
    """
    tool = SSHCommandTool(
        allowed_device_patterns=allowed_device_patterns,
        db_session_factory=db_session_factory,
        encryption_key=encryption_key,
    )

    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        handler=tool.execute,
        requires_approval=tool.requires_approval,
        risk_level=tool.risk_level,
    )
