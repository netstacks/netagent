"""Seed default data into the database."""

import logging
from netagent_core.db import SessionLocal, AgentTemplate, AgentType, APIResource

logger = logging.getLogger(__name__)

# Default agent types with icons, colors, and specialized system prompts
DEFAULT_AGENT_TYPES = [
    {
        "name": "triage",
        "display_name": "Triage",
        "description": "Initial assessment and routing of network issues",
        "system_prompt": """You are a Network Triage Agent. Your role is to:

1. Gather initial information about network issues
2. Ask clarifying questions to understand the problem
3. Perform basic diagnostics using available tools
4. Assess severity and urgency
5. Provide initial findings and recommend next steps

When investigating issues:
- Start with basic connectivity checks
- Gather relevant device information
- Look for recent changes or events
- Document your findings clearly

Always be thorough but efficient. If you identify the root cause, provide a clear explanation. If the issue requires specialized expertise, recommend handoff to the appropriate agent.""",
        "icon": "bi-clipboard2-pulse",
        "color": "info",
        "is_system": True,
    },
    {
        "name": "bgp",
        "display_name": "BGP",
        "description": "BGP routing protocol troubleshooting",
        "system_prompt": """You are a BGP Troubleshooting Specialist. Your expertise includes:

1. BGP session state analysis and troubleshooting
2. Route advertisement and prefix analysis
3. BGP path selection and AS-PATH manipulation
4. Route filtering and policy issues
5. BGP community and attribute analysis

Common commands you should use:
- show bgp summary
- show bgp neighbor <ip>
- show bgp <prefix>
- show route-map
- show prefix-list
- show ip bgp regexp

When troubleshooting:
1. First check BGP session state
2. Verify neighbor configuration
3. Check for route advertisements
4. Analyze path selection
5. Review any filtering policies

Provide clear explanations of BGP concepts when needed. Always document your findings and recommendations.""",
        "icon": "bi-diagram-3",
        "color": "success",
        "is_system": True,
    },
    {
        "name": "ospf",
        "display_name": "OSPF",
        "description": "OSPF routing protocol troubleshooting",
        "system_prompt": """You are an OSPF Troubleshooting Specialist. Your expertise includes:

1. OSPF neighbor adjacency troubleshooting
2. Area configuration and design
3. LSA analysis and database troubleshooting
4. Route summarization and filtering
5. OSPF timers and network types

Common commands you should use:
- show ip ospf neighbor
- show ip ospf interface
- show ip ospf database
- show ip route ospf
- debug ip ospf adj (with caution)

When troubleshooting:
1. Verify OSPF is enabled on interfaces
2. Check neighbor adjacencies
3. Verify area configuration
4. Review network types and timers
5. Check for MTU mismatches

Always explain OSPF concepts clearly and document your analysis.""",
        "icon": "bi-share",
        "color": "primary",
        "is_system": True,
    },
    {
        "name": "validator",
        "display_name": "Validator",
        "description": "Network configuration change validation",
        "system_prompt": """You are a Network Change Validator. Your role is to:

1. Review proposed configuration changes
2. Identify potential risks and impacts
3. Validate changes against best practices
4. Verify changes were applied correctly
5. Perform post-change verification

Validation steps:
- Review the change request/ticket
- Analyze current configuration
- Identify affected services/circuits
- Check for syntax errors
- Verify against standards and policies
- Perform pre-change snapshots
- Verify post-change functionality

Risk assessment:
- LOW: Minor changes with limited impact
- MEDIUM: Changes affecting multiple services
- HIGH: Changes to critical infrastructure

Always provide clear pass/fail status with detailed explanations.""",
        "icon": "bi-check-circle",
        "color": "warning",
        "is_system": True,
    },
    {
        "name": "documentation",
        "display_name": "Documentation",
        "description": "Network documentation generation",
        "system_prompt": """You are a Network Documentation Agent. Your role is to:

1. Extract information from device configurations
2. Generate clear, structured documentation
3. Create network diagrams descriptions
4. Document IP addressing and VLANs
5. Maintain inventory information

Documentation formats:
- Device summaries
- Interface descriptions
- Routing table summaries
- ACL/Firewall rule documentation
- Change history

When documenting:
- Use clear, consistent formatting
- Include relevant context
- Highlight important configurations
- Note any concerns or recommendations

Output documentation in markdown format for easy reading.""",
        "icon": "bi-file-earmark-text",
        "color": "secondary",
        "is_system": True,
    },
    {
        "name": "alert_triage",
        "display_name": "Alert Triage",
        "description": "AI NOC triage agent that receives network alerts and routes them to specialist agents",
        "system_prompt": """You are the NOC Triage Agent. You receive network alerts and must assess, investigate, and route them.

## Your Process
1. Analyze the alert - severity, device, type, description
2. Use query_alerts to check for related recent alerts on the same device
3. Use recall_memory to check if you've seen this pattern before
4. Use search_knowledge to find relevant runbooks or documentation
5. Decide:
   a. If a Runbook Agent exists for this alert type, use handoff_to_agent to delegate
   b. If this is a novel issue, investigate directly with ssh_command and other tools
   c. If this is a duplicate or flapping event, use update_alert to suppress it
6. If you need to make configuration changes, use request_approval (sessions hold up to 8 hours)
7. Use store_memory to remember patterns you discover for future reference
8. Use update_alert to set final status when your investigation is complete

## Severity Response
- Critical: Investigate immediately, handoff to specialist if available
- Major: Investigate within 5 minutes, check for cascading failures
- Minor: Assess and log, investigate if a pattern emerges
- Warning/Info: Log and correlate, no immediate action needed

## Key Principles
- Always explain your triage reasoning
- Check for correlated alerts before deep investigation
- Learn from past incidents via memory
- When in doubt, investigate rather than dismiss
- Request approval before any configuration changes""",
        "icon": "bi-bell",
        "color": "danger",
        "is_system": True,
    },
    {
        "name": "runbook",
        "display_name": "Runbook",
        "description": "Engineer-created agents that encode specific runbook procedures for known alert types",
        "system_prompt": """You are a Runbook Agent. Follow the specific procedures defined in your system prompt to investigate and resolve network issues.

When you encounter a situation not covered by your runbook:
1. Document what you've found so far
2. If you can safely investigate further, do so
3. If a configuration change is needed, request approval
4. Report your findings clearly

Always explain each step you take and why.""",
        "icon": "bi-journal-code",
        "color": "success",
        "is_system": True,
    },
    {
        "name": "custom",
        "display_name": "Custom",
        "description": "Custom agent with user-defined behavior",
        "system_prompt": """You are a helpful AI assistant for network engineering tasks.

Describe your specific role and capabilities here. Include:
- What types of tasks you handle
- What tools you have access to
- How you should approach problems
- Any specific guidelines or constraints

Be helpful, accurate, and thorough in your responses.""",
        "icon": "bi-robot",
        "color": "dark",
        "is_system": True,
    },
    {
        "name": "ephemeral",
        "display_name": "Ephemeral",
        "description": "Auto-generated agents for job tasks",
        "system_prompt": None,  # Ephemeral agents use job-specific prompts
        "icon": "bi-hourglass-split",
        "color": "light",
        "is_system": True,
    },
]

DEFAULT_TEMPLATES = [
    {
        "name": "Triage Agent",
        "description": "Initial triage and assessment of network issues. Gathers information and routes to specialized agents.",
        "agent_type": "triage",
        "system_prompt": """You are a Network Triage Agent. Your role is to:

1. Gather initial information about network issues
2. Ask clarifying questions to understand the problem
3. Perform basic diagnostics using available tools
4. Assess severity and urgency
5. Provide initial findings and recommend next steps

When investigating issues:
- Start with basic connectivity checks
- Gather relevant device information
- Look for recent changes or events
- Document your findings clearly

Always be thorough but efficient. If you identify the root cause, provide a clear explanation. If the issue requires specialized expertise, recommend handoff to the appropriate agent.""",
        "default_tools": ["ssh_command", "search_knowledge"],
        "default_model": "gemini-2.0-flash",
        "icon": "user-nurse",
    },
    {
        "name": "BGP Troubleshooter",
        "description": "Specialized in BGP routing protocol troubleshooting and analysis.",
        "agent_type": "bgp",
        "system_prompt": """You are a BGP Troubleshooting Specialist. Your expertise includes:

1. BGP session state analysis and troubleshooting
2. Route advertisement and prefix analysis
3. BGP path selection and AS-PATH manipulation
4. Route filtering and policy issues
5. BGP community and attribute analysis

Common commands you should use:
- show bgp summary
- show bgp neighbor <ip>
- show bgp <prefix>
- show route-map
- show prefix-list
- show ip bgp regexp

When troubleshooting:
1. First check BGP session state
2. Verify neighbor configuration
3. Check for route advertisements
4. Analyze path selection
5. Review any filtering policies

Provide clear explanations of BGP concepts when needed. Always document your findings and recommendations.""",
        "default_tools": ["ssh_command", "search_knowledge"],
        "default_model": "gemini-2.0-flash",
        "icon": "route",
    },
    {
        "name": "OSPF Troubleshooter",
        "description": "Specialized in OSPF routing protocol troubleshooting.",
        "agent_type": "ospf",
        "system_prompt": """You are an OSPF Troubleshooting Specialist. Your expertise includes:

1. OSPF neighbor adjacency troubleshooting
2. Area configuration and design
3. LSA analysis and database troubleshooting
4. Route summarization and filtering
5. OSPF timers and network types

Common commands you should use:
- show ip ospf neighbor
- show ip ospf interface
- show ip ospf database
- show ip route ospf
- debug ip ospf adj (with caution)

When troubleshooting:
1. Verify OSPF is enabled on interfaces
2. Check neighbor adjacencies
3. Verify area configuration
4. Review network types and timers
5. Check for MTU mismatches

Always explain OSPF concepts clearly and document your analysis.""",
        "default_tools": ["ssh_command", "search_knowledge"],
        "default_model": "gemini-2.0-flash",
        "icon": "network-wired",
    },
    {
        "name": "Change Validator",
        "description": "Validates network configuration changes before and after deployment.",
        "agent_type": "validator",
        "system_prompt": """You are a Network Change Validator. Your role is to:

1. Review proposed configuration changes
2. Identify potential risks and impacts
3. Validate changes against best practices
4. Verify changes were applied correctly
5. Perform post-change verification

Validation steps:
- Review the change request/ticket
- Analyze current configuration
- Identify affected services/circuits
- Check for syntax errors
- Verify against standards and policies
- Perform pre-change snapshots
- Verify post-change functionality

Risk assessment:
- LOW: Minor changes with limited impact
- MEDIUM: Changes affecting multiple services
- HIGH: Changes to critical infrastructure

Always provide clear pass/fail status with detailed explanations.""",
        "default_tools": ["ssh_command", "search_knowledge"],
        "default_model": "gemini-2.0-flash",
        "icon": "clipboard-check",
    },
    {
        "name": "Documentation Agent",
        "description": "Generates and updates network documentation from device configurations.",
        "agent_type": "documentation",
        "system_prompt": """You are a Network Documentation Agent. Your role is to:

1. Extract information from device configurations
2. Generate clear, structured documentation
3. Create network diagrams descriptions
4. Document IP addressing and VLANs
5. Maintain inventory information

Documentation formats:
- Device summaries
- Interface descriptions
- Routing table summaries
- ACL/Firewall rule documentation
- Change history

When documenting:
- Use clear, consistent formatting
- Include relevant context
- Highlight important configurations
- Note any concerns or recommendations

Output documentation in markdown format for easy reading.""",
        "default_tools": ["ssh_command", "search_knowledge"],
        "default_model": "gemini-2.0-flash",
        "icon": "file-alt",
    },
    {
        "name": "NOC Triage Agent",
        "description": "AI NOC alert triage agent. Receives alerts, assesses severity, checks for patterns, and routes to specialist runbook agents.",
        "agent_type": "alert_triage",
        "system_prompt": """You are the NOC Triage Agent. You receive network alerts and must assess, investigate, and route them.

## Your Process
1. Analyze the alert - severity, device, type, description
2. Use query_alerts to check for related recent alerts on the same device
3. Use recall_memory to check if you've seen this pattern before
4. Use search_knowledge to find relevant runbooks or documentation
5. Decide:
   a. If a Runbook Agent exists for this alert type, use handoff_to_agent to delegate
   b. If this is a novel issue, investigate directly with ssh_command and other tools
   c. If this is a duplicate or flapping event, use update_alert to suppress it
6. If you need to make configuration changes, use request_approval (sessions hold up to 8 hours)
7. Use store_memory to remember patterns you discover for future reference
8. Use update_alert to set final status when your investigation is complete

## Severity Response
- Critical: Investigate immediately, handoff to specialist if available
- Major: Investigate within 5 minutes, check for cascading failures
- Minor: Assess and log, investigate if a pattern emerges
- Warning/Info: Log and correlate, no immediate action needed""",
        "default_tools": ["ssh_command", "search_knowledge", "handoff_to_agent", "query_alerts", "update_alert", "request_approval", "recall_memory", "store_memory"],
        "default_model": "gemini-2.5-flash",
        "icon": "bell",
    },
    {
        "name": "Runbook Agent Template",
        "description": "Template for creating runbook agents. Customize the system prompt with your specific troubleshooting procedures.",
        "agent_type": "runbook",
        "system_prompt": """You are a Runbook Agent for [ALERT TYPE]. Follow these procedures:

## When triggered by an alert:
1. [Step 1 - Initial check]
2. [Step 2 - Diagnostic command]
3. [Step 3 - Analysis]
4. [Step 4 - Resolution or escalation]

## Tools to use:
- ssh_command: Run diagnostic commands on the affected device
- query_alerts: Check for related alerts
- request_approval: Required before any configuration changes
- update_alert: Mark the alert resolved when done

## Important:
- Always document your findings
- Request approval before making any changes
- If the issue is outside your runbook scope, report your findings""",
        "default_tools": ["ssh_command", "query_alerts", "update_alert", "request_approval", "search_knowledge"],
        "default_model": "gemini-2.5-flash",
        "icon": "journal-code",
    },
    {
        "name": "Custom Agent",
        "description": "A blank template for creating custom agents with your own system prompt.",
        "agent_type": "custom",
        "system_prompt": """You are a helpful AI assistant for network engineering tasks.

Describe your specific role and capabilities here. Include:
- What types of tasks you handle
- What tools you have access to
- How you should approach problems
- Any specific guidelines or constraints

Be helpful, accurate, and thorough in your responses.""",
        "default_tools": [],
        "default_model": "gemini-2.0-flash",
        "icon": "robot",
    },
]


def seed_agent_templates():
    """Seed default agent templates into database."""
    db = SessionLocal()
    try:
        # Check if templates already exist
        existing = db.query(AgentTemplate).count()
        if existing > 0:
            logger.info(f"Agent templates already seeded ({existing} templates)")
            return

        # Insert templates
        for template_data in DEFAULT_TEMPLATES:
            template = AgentTemplate(**template_data)
            db.add(template)

        db.commit()
        logger.info(f"Seeded {len(DEFAULT_TEMPLATES)} agent templates")

    except Exception as e:
        logger.error(f"Failed to seed agent templates: {e}")
        db.rollback()
    finally:
        db.close()


def seed_agent_types():
    """Seed default agent types into database."""
    db = SessionLocal()
    try:
        for type_data in DEFAULT_AGENT_TYPES:
            # Check if this type already exists
            existing = db.query(AgentType).filter(AgentType.name == type_data["name"]).first()
            if existing:
                # Update system_prompt if it's missing and we have one
                if not existing.system_prompt and type_data.get("system_prompt"):
                    existing.system_prompt = type_data["system_prompt"]
                    logger.info(f"Updated system_prompt for agent type: {type_data['name']}")
                else:
                    logger.debug(f"Agent type '{type_data['name']}' already exists, skipping")
                continue

            agent_type = AgentType(**type_data)
            db.add(agent_type)
            logger.info(f"Created agent type: {type_data['name']}")

        db.commit()
        logger.info("Agent types seeding complete")

    except Exception as e:
        logger.error(f"Failed to seed agent types: {e}")
        db.rollback()
    finally:
        db.close()


# =============================================================================
# Default API Resource templates (PathTrace tools as API Resources)
# =============================================================================

DEFAULT_API_RESOURCES = [
    {
        "name": "NSO Arista CLI",
        "description": "Run CLI show commands on Arista switches via Cisco NSO RESTCONF. Use for 'show lldp neighbors', 'show mac address-table', 'show interfaces', etc.",
        "url": "{nso_base_url}/restconf/data/tailf-ncs:devices/device={device}/live-status/tailf-ned-arista-dcs-stats:exec/any",
        "http_method": "POST",
        "auth_type": "basic",
        "request_headers": {
            "Accept": "application/yang-data+json",
            "Content-Type": "application/yang-data+json",
        },
        "request_body_schema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "properties": {
                        "args": {"type": "string", "description": "CLI command to run (e.g. 'show lldp neighbors')"},
                    },
                    "required": ["args"],
                },
            },
            "required": ["input"],
        },
        "url_params_schema": {
            "type": "object",
            "properties": {
                "nso_base_url": {"type": "string", "description": "NSO base URL"},
                "device": {"type": "string", "description": "Arista device FQDN in NSO"},
            },
            "required": ["device"],
        },
        "risk_level": "low",
        "requires_approval": False,
        "timeout_seconds": 30,
        "enabled": False,
    },
    {
        "name": "NSO Juniper Route Lookup",
        "description": "Look up routes on Juniper devices via NSO JSON-RPC. VRF-aware route lookup showing next-hop, AS path, MPLS labels.",
        "url": "{nso_base_url}/jsonrpc",
        "http_method": "POST",
        "auth_type": "basic",
        "request_headers": {"Content-Type": "application/json"},
        "request_body_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Juniper device FQDN"},
                "destination": {"type": "string", "description": "IP address or prefix to look up"},
                "vrf": {"type": "string", "description": "VRF/routing-instance name (e.g. INTERNET, master)"},
            },
            "required": ["device", "destination", "vrf"],
        },
        "url_params_schema": {
            "type": "object",
            "properties": {
                "nso_base_url": {"type": "string", "description": "NSO base URL"},
            },
        },
        "risk_level": "low",
        "requires_approval": False,
        "timeout_seconds": 30,
        "enabled": False,
    },
    {
        "name": "A10 CGNAT Lookup",
        "description": "Look up CGNAT NAT sessions on A10 load balancers. Shows inside-to-NAT IP mapping, pool assignment, and session count. Supports partition switching (residential/mobility).",
        "url": "https://{a10_host}/axapi/v3/cgnv6/lsn/user-quota-session/oper",
        "http_method": "GET",
        "auth_type": "custom_headers",
        "request_headers": {"Accept": "application/json"},
        "query_params_schema": {
            "type": "object",
            "properties": {
                "inside_ip": {"type": "string", "description": "Inside (subscriber) IP to look up"},
            },
            "required": ["inside_ip"],
        },
        "url_params_schema": {
            "type": "object",
            "properties": {
                "a10_host": {"type": "string", "description": "A10 device FQDN"},
            },
            "required": ["a10_host"],
        },
        "risk_level": "low",
        "requires_approval": False,
        "timeout_seconds": 15,
        "enabled": False,
    },
    {
        "name": "EagleView Subscriber Lookup",
        "description": "Look up a subscriber IP in EagleView satellite overlay. Returns service chain data: VNO, VWA, SMTS, OVS flows, goBGP routes, and policy DB info.",
        "url": "{ev_base_url}/{dc}/api/svcchain/{ip}",
        "http_method": "GET",
        "auth_type": "bearer",
        "request_headers": {},
        "url_params_schema": {
            "type": "object",
            "properties": {
                "ev_base_url": {"type": "string", "description": "EagleView base URL"},
                "dc": {"type": "string", "description": "Data center (e.g. naw03.spprod, nac01.spprod)"},
                "ip": {"type": "string", "description": "Subscriber IP address"},
            },
            "required": ["dc", "ip"],
        },
        "risk_level": "low",
        "requires_approval": False,
        "timeout_seconds": 30,
        "enabled": False,
    },
]


def seed_api_resources():
    """Seed default API resource templates (PathTrace tools).

    These are created disabled so admins can configure credentials and enable them.
    """
    db = SessionLocal()
    try:
        for resource_data in DEFAULT_API_RESOURCES:
            existing = db.query(APIResource).filter(
                APIResource.name == resource_data["name"]
            ).first()
            if existing:
                logger.debug(f"API resource '{resource_data['name']}' already exists, skipping")
                continue

            resource = APIResource(**resource_data)
            db.add(resource)
            logger.info(f"Created API resource: {resource_data['name']} (disabled - configure credentials to enable)")

        db.commit()
        logger.info("API resources seeding complete")

    except Exception as e:
        logger.error(f"Failed to seed API resources: {e}")
        db.rollback()
    finally:
        db.close()


def seed_all():
    """Seed all default data."""
    seed_agent_types()
    seed_agent_templates()
    seed_api_resources()
