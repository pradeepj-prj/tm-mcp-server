# TM Skills MCP Server

An [MCP](https://modelcontextprotocol.io/) server that exposes a Talent Management Skills API as tools for AI assistants. Built for [Joule](https://www.sap.com/products/artificial-intelligence/ai-assistant.html) agents on SAP BTP, but works with any MCP-compatible client.

## How it works

```
AI Assistant (Joule / Claude / etc.)
        │
        │  MCP protocol over SSE
        ▼
┌─────────────────────┐
│  TM Skills MCP      │  ← This project
│  Server (SSE)       │
└────────┬────────────┘
         │  HTTP + API key
         ▼
┌─────────────────────┐
│  TM Skills API      │  ← Separate FastAPI service
│  (REST)             │
└────────┬────────────┘
         │
         ▼
    PostgreSQL
```

The MCP server is a thin wrapper — it translates MCP tool calls into HTTP requests to the TM Skills API. Authentication, rate limiting, and input validation all happen at the API layer.

## Quick start

```bash
pip install -e .
cp .env.example .env       # Set TM_API_BASE_URL and TM_API_KEY
python server.py           # SSE server on http://localhost:8080/sse
```

For interactive testing with the MCP Inspector:

```bash
pip install mcp[cli]       # Inspector tooling (not needed for production)
mcp dev server.py
```

## Connecting an AI assistant

Point any MCP client at the SSE endpoint:

```
https://tm-skills-mcp.cfapps.ap10.hana.ondemand.com/sse
```

The client will discover 13 tools, 2 resources, and 3 prompt templates automatically via the MCP handshake.

**In Joule Studio:** Add the URL above as an MCP tool server when building a Joule Agent.

## Tools

13 read-only tools, one per API endpoint. The AI assistant chains them to answer talent questions — for example, `browse_skills("Python")` to get the skill ID, then `get_top_experts(skill_id=1)` to find experts.

### Employee tools

| Tool | What it answers |
|------|----------------|
| `get_employee_skills` | What skills does this person have? |
| `get_top_skills` | What are their strongest skills? |
| `get_evidence_inventory` | What evidence exists across all their skills? |
| `get_skill_evidence` | Why do we think they're proficient in a specific skill? |

### Skill tools

| Tool | What it answers |
|------|----------------|
| `browse_skills` | What skills exist in the catalog? (start here to find IDs) |
| `get_top_experts` | Who are the best people for this skill? |
| `get_skill_coverage` | How is proficiency distributed across the org? |
| `get_evidence_backed_candidates` | Who has this skill AND strong evidence to prove it? |
| `get_stale_skills` | Whose skill records need revalidation? |
| `get_cooccurring_skills` | What other skills commonly appear alongside this one? |
| `search_talent` | Who has ALL of these skills? (multi-skill AND search) |

### Org tools

| Tool | What it answers |
|------|----------------|
| `get_org_skill_summary` | What are the top skills in this org unit? |
| `get_org_skill_experts` | Who in this org has a specific skill? |

## Resources and prompts

**Resources** provide static context that the AI can reference:

| URI | Content |
|-----|---------|
| `tm://schema` | Database schema DDL |
| `tm://business-questions` | Business questions catalog with API mappings |

**Prompts** are reusable templates that script multi-step tool chains:

| Prompt | Example use |
|--------|-------------|
| `find_experts(skill_name)` | "Find the top Python experts and show their evidence" |
| `analyze_employee(employee_id)` | "Build a comprehensive talent profile for EMP000001" |
| `org_talent_review(org_unit_id)` | "Assess the talent landscape in ORG030" |

## Configuration

All settings come from environment variables (or a `.env` file locally):

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8080` | Server port (CF sets this automatically) |
| `TM_API_BASE_URL` | `http://localhost:8000` | TM Skills API URL |
| `TM_API_KEY` | *(empty)* | API key for the TM Skills API |
| `TM_API_TIMEOUT` | `30.0` | HTTP request timeout in seconds |

## Deploy to Cloud Foundry

```bash
./deploy.sh
```

The script reads the API key from `../tm_app/.api-key`, pushes the app without starting it, injects the key via `cf set-env`, then starts the app. This keeps the secret out of `manifest.yml` and version control.

### Manual deploy

```bash
cf push --no-start
cf set-env tm-skills-mcp TM_API_KEY "your-api-key"
cf start tm-skills-mcp
```

### Live deployment

| | URL |
|-|-----|
| **MCP endpoint** | `https://tm-skills-mcp.cfapps.ap10.hana.ondemand.com/sse` |
| **TM Skills API** | `https://tm-skills-api.cfapps.ap10.hana.ondemand.com` |
| **CF space** | `SEAIO_dial-3-0-zme762l7 / dev` |

## ID formats

These are validated by the API:

- **Employee IDs:** `EMP` + 6 digits (e.g. `EMP000001`)
- **Org IDs:** `ORG` + 1-4 digits + optional letter (e.g. `ORG030`, `ORG031B`)
- **Skill IDs:** numeric (e.g. `1`, `42`)
- **Categories:** `technical`, `functional`, `leadership`, `domain`, `tool`, `other`

## Tech stack

- **MCP SDK:** [mcp](https://pypi.org/project/mcp/) (Python SDK with FastMCP)
- **HTTP client:** [httpx](https://www.python-httpx.org/) (async)
- **Configuration:** [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) (`.env` + env vars)
- **Transport:** SSE (Server-Sent Events)
- **Python:** 3.10+
