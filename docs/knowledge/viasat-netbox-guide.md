# Viasat NetBox Knowledge Base

## Overview

This document provides comprehensive information about the Viasat NetBox instance (`netbox.gi-nw.viasat.io`) for the NetAgent NetBox Specialist AI agent. This NetBox manages Viasat's global satellite communication infrastructure, including gateways, core nodes, backbone networks, and associated network devices.

---

## Quick Statistics

| Category | Count |
|----------|-------|
| Sites | 1,677 |
| Devices | 12,103 |
| Device Types | 184 |
| Device Roles | 113 |
| Manufacturers | 32 |
| Regions | 94 |
| Tenants | 18 |
| Platforms | 9 |
| Tags | 64 |
| VLANs | 743 |
| IP Prefixes | 53,420 |
| VRFs | 983 |
| Circuits | 1,510 |
| Circuit Providers | 505 |
| Custom Fields | 42 |

---

## Organizational Structure

### Regions (Geographic Hierarchy)

The infrastructure spans globally with a hierarchical region structure:

| Region Code | Description | Sites |
|-------------|-------------|-------|
| `GLOBAL` | Global Whole Earth Region | 1,675 |
| `AMER` | Americas | 781 |
| `EMEA` | Europe, Middle East, Africa | Variable |
| `nae` | North America East | 36 |
| `naw` | North America West | 23 |
| `nac` | North America Central | 13 |
| `aps` | Asia Pacific South (Australia) | 20+ |
| `apc` | Asia Pacific Central (China/Asia) | 16+ |

### Site Naming Conventions

Sites follow a structured naming pattern:
- **`<region><number>`** - Example: `abq01`, `ama01`, `aps22`
- Region codes are typically 3 characters (airport codes or geographic abbreviations)
- Sequential numbers indicate multiple sites in same region

### Key Site Types (Identified by Tags)

| Tag | Description | Color |
|-----|-------------|-------|
| `gw` | Satellite Gateway | Blanched Almond |
| `cn` | Core Node | Cyan |
| `san` | Satellite Access Node | Sienna |
| `bnn` | Backbone Node | Antique White |
| `ppn` | Phy Processing Node | Brown |
| `igw` | Internet Gateway | Blue |
| `dc` | Data Centre | Azure |
| `dcs` | DCS Related Entity | Cyan |
| `ttnc` | TT&C Entity (Telemetry, Tracking & Command) | Gray |
| `abbp` | ABBP Gateway Network | Gray |
| `anchor-dt` | Anchor DT | Misty Rose |

### Flight/Generation Tags (VS3)

| Tag | Description |
|-----|-------------|
| `f1` | VS3 Flight 1 - North America |
| `f2` | VS3 Flight 2 - EMEA |
| `f3` | VS3 Flight 3 - APAC |
| `vs3` | Viasat-3 Generation |

---

## Tenants (Business Units/Partners)

| Tenant | Description |
|--------|-------------|
| `Viasat` | Primary tenant - Viasat corporate |
| `ChinaSAT` | ChinaSat/China Satcom partnership |
| `AU Mobility` | Australian Mobility services |
| `NBN LTSS` | NBN Long Term Satellite Service (Australia) |
| `NBN GTB` | NBN Test LAB/PreProd (Carlsbad) |
| `Inmarsat` | Inmarsat integration |
| `Inmarsat Gov't Svcs` | Inmarsat Government Services |
| `KaSAT` | KaSAT satellite network |
| `Avanti` | Avanti Communications partnership |
| `SKYLOGIC` | Skylogic operations |
| `Government Services` | Government sector services |
| `CMS` | GC/TNS Configuration Management System |
| `DCS` | Data Center Services |
| `InfraDev` | InfraDev LAB |

---

## Device Infrastructure

### Supported Platforms

| Platform | Slug | Typical Usage |
|----------|------|---------------|
| Cisco IOS-XR | `cisco_xr` | Core routing (NCS series) |
| Cisco IOS | `cisco_ios` | Legacy/ISR devices |
| Cisco NX-OS | `cisco_nxos` | Nexus data center switching |
| Juniper Junos | `juniper_junos` | Aggregation/distribution switching |
| Arista EOS | `arista_eos` | Data plane switching |
| Fortinet | `fortinet` | Firewall/security |
| A10 | `a10` | Load balancing |
| Linux | `linux` | Compute/servers |
| Meraki | `meraki` | Wireless/edge |

### Key Device Roles

| Role | Slug | Count | Description |
|------|------|-------|-------------|
| COMP | `comp` | 840 | Compute Node |
| CN | `cn` | 719 | Core Node Devices |
| GTB | `gtb` | 989 | Global Transport Backbone MPLS |
| DECODER | `decoder` | 333 | Decoder Compute Node |
| AGGS | `aggs` | 220 | Aggregation Switch |
| FRWD | `frwd` | 217 | Firewall |
| IGW | `igw` | 228 | Internet Gateway |
| BNN | `bnn` | 209 | Backbone Node |
| INAR | `inar` | 138 | Internal Aggregation Router |
| MGLF | `mglf` | 123 | Management Leaf |
| BMS | `bms` | 122 | Backbone Management Switch |
| OBLF | `oblf` | 121 | Out of Band Leaf |
| ASLA | `asla` | 96 | Metronid ASLA Monitoring |
| EXAR | `exar` | 90 | External Aggregation Router |
| GWRFA | `gwrfa` | 68 | Gateway RFA |
| SSW | `ssw` | 68 | SAN Switch |
| IAPS | `iaps` | 60 | Infrastructure Application Switch |
| DCAR | `dcar` | 47 | Data Center Aggregation Router |
| DMSP | `dmsp` | 42 | Data Management Spine |
| NWLF | `nwlf` | 41 | Network Leaf |

### Major Manufacturers

| Manufacturer | Product Focus |
|--------------|---------------|
| **Juniper** | Aggregation switches (QFX, EX series), Routers |
| **Cisco** | Core routing (NCS-5500), Nexus switching, ISR |
| **Arista** | Data center switching (DCS-7xxx series) |
| **Fortinet** | Firewalls (FortiGate) |
| **Infinera** | DWDM optical transport (GX-42, XT-3312) |
| **A10** | Load balancers (Thunder series) |
| **Supermicro** | Compute servers |
| **Dell** | Compute servers |
| **Adva** | Optical networking (FSP-3000) |

### Common Device Types

**Juniper:**
- QFX5210-64C - Aggregation/spine switches
- EX4500-40F - Legacy aggregation
- EX4300-48T, EX4400-48T - Access switching
- ACX7024X - Provider edge

**Cisco:**
- NCS-5501, NCS-5501-SE - Provider routing
- NCS-5504, NCS-5508, NCS-5516 - High-capacity routing
- NCS-55A1-36H-S/SE-S - High-density provider edge
- N9K-93180YC-FX3 - Data center switching
- ISR4431, ISR1921 - Branch routing

**Arista:**
- DCS-7050 series - Data center leaf/spine
- DCS-7280 series - High-performance routing
- DCS-7260CX3-64-F - 100G switching

---

## Network Infrastructure

### Circuit Types

| Type | Slug | Description |
|------|------|-------------|
| VS3 LIT | `vs3-lit` | VS3 SAN Sites LIT Circuits |
| FTN-BACKBONE | `ftn-backbone` | Circuits via Infinera optical |
| FTN-ACCESS | `ftn-access` | Circuits via ADVA access network |
| NNI | `nni` | Network to Network Interconnects |
| LEASED | `leased` | Leased line circuits from providers |

### Major Circuit Providers (505 total)

Key providers include:
- AT&T
- ARELION (Telia)
- Avanti
- AWS (Direct Connect)
- Lumen/CenturyLink
- Equinix
- Zayo
- Various regional telcos

### VRF Structure (983 VRFs)

Common VRF naming patterns:
- `*_DATAPLANE` - Data plane traffic
- `*_MGMT` - Management traffic
- `*_BACKHAUL` - Backhaul connectivity
- `*_VNO` - Virtual Network Operator
- `*_NNI` - Network-to-Network interface
- `*_EXT` - External connectivity
- `*_SUB` - Subscriber traffic
- `BBCDEV_*` - Broadband development

---

## Custom Fields

Important custom fields for device/network management:

| Field | Type | Usage |
|-------|------|-------|
| `environment` | Selection | Network environment (prod/dev/lab) |
| `network` | Multi-select | Device network assignment |
| `monitoring` | Boolean | Monitoring enabled flag |
| `config_backup` | URL | Config backup location |
| `asn` | Multi-object | Device ASN assignment |
| `asn_role` | Text | Device ASN role |
| `vrfs` | Multi-object | Associated VRFs |
| `satellites` | Multi-select | Satellites served |
| `service_id` | Integer | Service identifier |
| `nso_device_service_instances` | Multi-select | NSO configuration instances |
| `config_profile` | Multi-select | Configuration profiles |
| `community_name` | Long text | Known BGP community names |
| `patchmanager_entity_id` | Text | Patch Manager ID |
| `ftn_site_code` | Text | FTN Site Code |
| `facility_provider` | Text | Site facility provider |
| `facility_site_code` | Text | Facility site code |
| `slack_channel` | Text | Team Slack channel |
| `contacts` | Multi-object | Contact details |

---

## Query Patterns for NetAgent

### Find Gateway Sites
```
object_type: dcim.site
filters: {"tag": "gw"}
```

### Find Active Devices at a Site
```
object_type: dcim.device
filters: {"site": "<site_name>", "status": "active"}
```

### Find Devices by Role
```
object_type: dcim.device
filters: {"role": "<role_slug>"}
# Example roles: aggs, frwd, cn, gtb, inar, dcar
```

### Find Core Nodes
```
object_type: dcim.device
filters: {"role": "cn"}
# OR by tag
object_type: dcim.site
filters: {"tag": "cn"}
```

### Find Devices by Platform
```
object_type: dcim.device
filters: {"platform": "juniper_junos"}
# Options: cisco_xr, cisco_ios, juniper_junos, arista_eos, fortinet
```

### Find Devices by Tenant
```
# First get sites for tenant
object_type: dcim.site
filters: {"tenant": "chinasat"}
# Then get devices at those sites
```

### Search Circuits by Provider
```
object_type: circuits.circuit
filters: {"provider": "at-t"}
```

### Find IP Prefixes for a Site
```
object_type: ipam.prefix
filters: {"site_id": <site_id>}
```

### Find VLANs
```
object_type: ipam.vlan
filters: {"name__ic": "FRWD"}  # Contains "FRWD"
```

---

## Device Naming Conventions

Devices follow consistent naming patterns:

### Standard Format
```
<role>-<env>.<site>.<domain>
```

Examples:
- `aggs01-gprod.abq01.gi-nw.viasat.io` - Production aggregation switch at ABQ
- `aggs01-gprpd.naw15.gi-nw.viasat.io` - Pre-production at NAW15
- `aggs01-boi-vs1.naw.sb2.viasat.io` - Legacy SB2 naming

### Environment Codes
- `gprod` - Global Production
- `gprpd` - Global Pre-Production
- `*-vs1`, `*-vs2` - ViaSat-1, ViaSat-2 generation

### Domain Patterns
- `gi-nw.viasat.io` - Ground Infrastructure Network
- `sb2.viasat.io` - SurfBeam 2 network
- Regional: `naw.*`, `nae.*`, `nac.*`

---

## Site Types Explained

### Gateway Sites (`gw` tag)
Satellite gateway locations that provide connectivity to satellites. Examples:
- `abq01` - Albuquerque Gateway
- `ama01` - Amarillo Gateway
- `hnl01` - Honolulu Gateway

### Core Nodes (`cn` tag)
Processing centers for satellite traffic. Often co-located with data centers:
- `aps07` - NextDC Sydney S2, Sydney PPN BNN CN
- `aps08` - Equinix Sydney SY5, Sydney PPN CN BNN

### Backbone Nodes (`bnn` tag)
Network backbone points in the fiber network:
- `aps06` - NextDC Canberra C1, BNN
- Various Australian BNN sites

### Data Centers (`dc` tag)
Colocation facilities:
- `aps02` - Sydney Equinix SY4
- `apc06` - Equinix Singapore SG1

### Public Cloud (`public-cloud` tag)
Cloud provider regions:
- `aps01` - AWS Asia Pacific (Sydney)
- `aps17` - Google GCP Sydney
- Various AWS/GCP/Azure regions

### TT&C Sites (`ttnc` tag)
Telemetry, Tracking & Command stations:
- `aps20` - Avoca Australia TT&C
- `aps21` - Wellcamp Australia TT&C

---

## Regional Site Examples

### North America West (NAW)
- `naw15` - Carlsbad DN, AfterBurner Lab
- `naw06` - Carlsbad W2 Building (NBN GTB)

### North America East (NAE)
- `nae05` - Viasat Germantown, FI Testbed
- `nae33` - SES-17 Gateway, London Ontario
- `nae37` - St. John's SES-17 ABBP Gateway

### Asia Pacific (APS/APC)
- `aps02` through `aps25` - Australian infrastructure
- `apc01` through `apc16` - ChinaSat infrastructure

### EMEA (EUE/EUW/EUC)
- `euc21` - Fucino Space Center, Italy
- `eue01` through `eue13` - European gateways
- `euw03` - SKL Madrid Gateway

### South America (SAE/BSB)
- `bsb01` through `bsb07` - Brasilia region
- `sae01` through `sae03` - São Paulo region

---

## Integration Notes

### NSO/CNC Integration
- Devices with `nso-controlled` tag have configurations managed by Cisco NSO/CNC
- Custom field `nso_device_service_instances` tracks active service instances

### Monitoring
- `monitoring` custom field indicates if device is monitored
- ASLA devices (role: `asla`) are Metronid monitoring probes

### Configuration Management
- `config_backup` field links to configuration storage
- `config_profile` field tracks applied configuration templates

---

## Best Practices for Queries

1. **Use field filtering** - Always specify `fields` parameter to minimize token usage
2. **Start with counts** - Use `limit=1` first to understand result size
3. **Filter by status** - Most queries should include `status: "active"`
4. **Use site relationships** - Get site first, then query related devices
5. **Leverage tags** - Tags are the primary way to categorize infrastructure
6. **Check tenant** - Filter by tenant for partner-specific queries

---

## Common Troubleshooting Scenarios

### "Find all firewalls at gateway sites"
1. Get sites with `gw` tag
2. For each site, get devices with role `frwd`

### "What devices are being decommissioned?"
```
filters: {"status": "decommissioning"}
```

### "Show me the Australia NBN infrastructure"
```
# Sites
filters: {"tenant": "nbn-ltss"}
# OR by tag
filters: {"tag": "nbn"}
```

### "What circuits connect to site X?"
```
object_type: circuits.circuittermination
filters: {"site_id": <site_id>}
```

---

*Last Updated: January 2026*
*NetBox Instance: netbox.gi-nw.viasat.io*
