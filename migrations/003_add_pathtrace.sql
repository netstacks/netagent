-- Migration: Add PathTrace Agent, MCP Servers, and Agent Type
-- Date: 2026-03-16
-- Description: Adds Network Path Tracer agent with NSO/A10/EagleView tools
--              and NetBox/Netdisco MCP server configurations

-- 1. Add PathTrace agent type
INSERT INTO agent_types (name, display_name, description, icon, color, is_system, system_prompt)
VALUES (
    'path_tracer',
    'Network Path Tracer',
    'Traces forwarding paths hop by hop through the Viasat network using NSO, A10 CGNAT, EagleView, NetBox, and Netdisco.',
    'bi-diagram-3',
    'info',
    true,
    'You are a Viasat satellite network path tracing agent. You trace forwarding paths hop by hop through the network using live device queries.

You understand:
- Viasat satellite network: CPE → Satellite → Gateway/SMTS → VWA → DCAR → underlay → EXAR → Internet
- VNOs: EXEDE (residential), EXEDE_MOBILITY, BIZAV_ARINC, GSD (NNI)
- DCARs: Juniper MX480 pairs with VRFs (SUB, VNO, HUB). Traffic hairpins across VRFs.
- Underlay: SubSwitch → Sandvine shaper (invisible) → NetSwitch → A10 CGNAT → DCAR(VNO) → EXAR
- Old Gen2 backbone: core routers → crsw (L2 bridge) for NNI/BizAv
- MPLS L3-VPN between DCAR and EXAR
- North/South of shaper terminology
- ECMP, FBF (Filter-Based Forwarding), BGP best path selection

Known devices:
- DCARs: dcar01-den.naw03.spprod.viasat.io, dcar01-chi.nac01.spprod.viasat.io
- EXARs: exar01-den.naw03.spprod.viasat.io, exar01-chi.nac01.spprod.viasat.io
- A10 CGNAT: cgnat01-den.naw03.spprod.viasat.io (partitions: VIASAT_RES_SUB, VIASAT_MOB)
- SubSwitches: subsw01-den.naw03.spprod.viasat.io
- NetSwitches: netsw01-den.naw03.spprod.viasat.io
- Core nodes: Denver (naw03), Chicago (nac01)

Strategy:
- Use eagleview_lookup first to identify subscribers
- Use nso_juniper_route for route lookups (specify VRF: EXEDE_SUB, EXEDE_VNO, INTERNET, etc.)
- Use nso_juniper_lldp to discover L2 topology
- Use nso_arista_exec for Arista switch queries
- Use a10_cgnat_lookup for NAT sessions (100.x IPs are CGNAT)
- Use netbox_search to find devices and resolve names
- Short device names are auto-resolved to FQDN via NetBox

VRF naming: {VNO_NAME}_{VRF_TYPE} (e.g. EXEDE_SUB, BIZAV_ARINC_VNO)
EXAR VRFs: INTERNET (residential), GSD (NNI customer), BIZAV_ARINC_NNI

NEVER assume the path. ALWAYS verify with actual route lookups at each hop.'
) ON CONFLICT (name) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    system_prompt = EXCLUDED.system_prompt,
    icon = EXCLUDED.icon,
    color = EXCLUDED.color;

-- 2. Add NetBox MCP server
INSERT INTO mcp_servers (name, description, base_url, transport, health_status, enabled)
VALUES (
    'NetBox',
    'NetBox device inventory, IP management, VRF lookups, and VNO plugin. Source of truth for device names and management IPs.',
    'http://internal-apitools-elb-cw-1573458742.us-east-1.elb.amazonaws.com:8000/mcp',
    'http',
    'unknown',
    true
) ON CONFLICT DO NOTHING;

-- 3. Add Netdisco MCP server
INSERT INTO mcp_servers (name, description, base_url, transport, health_status, enabled)
VALUES (
    'Netdisco',
    'L2 network discovery, MAC/ARP tracking, LLDP/CDP neighbors, device ports and VLANs via SNMP.',
    'http://internal-apitools-elb-cw-1573458742.us-east-1.elb.amazonaws.com:8003/mcp',
    'http',
    'unknown',
    true
) ON CONFLICT DO NOTHING;

-- 4. Create the PathTrace agent
INSERT INTO agents (
    name, description, agent_type, system_prompt, model, temperature,
    max_tokens, max_iterations, autonomy_level, allowed_tools,
    allowed_device_patterns, enabled, is_template
) VALUES (
    'Network Path Tracer',
    'Traces forwarding paths hop by hop through the Viasat network. Queries NSO (Juniper/Arista), A10 CGNAT, EagleView, NetBox, and Netdisco in real time.',
    'path_tracer',
    -- Use the agent type system prompt (same as above)
    (SELECT system_prompt FROM agent_types WHERE name = 'path_tracer'),
    'gemini-2.5-flash',
    0.1,
    8192,
    15,
    'execute',
    '["nso_juniper_route", "nso_juniper_lldp", "nso_juniper_vrfs", "nso_arista_exec", "a10_cgnat_lookup", "eagleview_lookup", "netbox_search", "ssh_command", "search_knowledge"]',
    '["*"]',
    true,
    false
) ON CONFLICT DO NOTHING;

-- 5. Create an agent template for PathTrace
INSERT INTO agents (
    name, description, agent_type, system_prompt, model, temperature,
    max_tokens, max_iterations, autonomy_level, allowed_tools,
    allowed_device_patterns, enabled, is_template
) VALUES (
    'Path Tracer Template',
    'Template: Network path tracing agent with all NSO/A10/EagleView tools enabled.',
    'path_tracer',
    (SELECT system_prompt FROM agent_types WHERE name = 'path_tracer'),
    'gemini-2.5-flash',
    0.1,
    8192,
    15,
    'execute',
    '["nso_juniper_route", "nso_juniper_lldp", "nso_juniper_vrfs", "nso_arista_exec", "a10_cgnat_lookup", "eagleview_lookup", "netbox_search", "ssh_command"]',
    '["*"]',
    true,
    true
) ON CONFLICT DO NOTHING;

-- 6. Link MCP servers to the PathTrace agent (update mcp_server_ids after insert)
-- This needs to be done after we know the MCP server IDs
DO $$
DECLARE
    netbox_id INTEGER;
    netdisco_id INTEGER;
    agent_id INTEGER;
BEGIN
    SELECT id INTO netbox_id FROM mcp_servers WHERE name = 'NetBox' LIMIT 1;
    SELECT id INTO netdisco_id FROM mcp_servers WHERE name = 'Netdisco' LIMIT 1;
    SELECT id INTO agent_id FROM agents WHERE name = 'Network Path Tracer' AND is_template = false LIMIT 1;

    IF agent_id IS NOT NULL AND netbox_id IS NOT NULL THEN
        UPDATE agents
        SET mcp_server_ids = COALESCE(
            (SELECT jsonb_agg(DISTINCT val) FROM (
                SELECT jsonb_array_elements(COALESCE(mcp_server_ids, '[]'::jsonb)) AS val
                UNION
                SELECT to_jsonb(netbox_id)
                UNION
                SELECT to_jsonb(netdisco_id)
            ) sub),
            '[]'::jsonb
        )
        WHERE id = agent_id;
    END IF;
END $$;
