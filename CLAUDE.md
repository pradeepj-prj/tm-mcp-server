# TM Skills MCP Server

An MCP (Model Context Protocol) server that exposes the Talent Management Skills API as tools, resources, and prompts for AI assistants (e.g. Joule agents on SAP BTP).

## Architecture

This MCP server wraps the **TM Skills REST API** (a separate FastAPI project at `../tm_app`). It does NOT access the database directly — all data flows through the HTTP API.

```
Joule Agent / AI Assistant ↔ MCP Server (SSE) ↔ HTTP ↔ TM Skills API (FastAPI) ↔ PostgreSQL
```

### Why wrap the API instead of querying the DB directly?
- The API has authentication (API key), rate limiting (60/min), access logging, and input validation
- Keeps the security boundary intact — the MCP server is just another API client
- No need to duplicate query logic or manage DB connections

## Tech Stack
- **MCP SDK:** `mcp` (Python SDK with FastMCP)
- **HTTP client:** `httpx` (async)
- **Configuration:** `pydantic-settings` (reads `.env` or env vars)
- **Transport:** SSE (Server-Sent Events) — HTTP-based, suitable for remote deployment

## Project Structure
```
tm_mcp_server/
├── CLAUDE.md              ← You are here
├── server.py              ← MCP server: tools, resources, prompts (SSE transport)
├── config.py              ← Configuration (host, port, API URL, API key)
├── pyproject.toml         ← Project metadata and dependencies
├── requirements.txt       ← Production deps for CF Python buildpack
├── runtime.txt            ← Python version pin for CF
├── Procfile               ← CF start command
├── manifest.yml           ← CF app config (memory, health check, env vars)
├── deploy.sh              ← Automated CF deployment with secret management
├── .cfignore              ← Excludes dev artifacts from CF upload
├── .env.example           ← Environment variable template
├── .gitignore
└── resources/
    ├── tm_schema.sql      ← TM database schema (served as MCP resource)
    └── business_questions.md ← Business questions catalog (served as MCP resource)
```

## Running Locally

```bash
cd /Users/I774404/tm_mcp_server
pip install -e .
cp .env.example .env       # Configure API URL and key
python server.py           # Starts SSE server on http://localhost:8080
```

The SSE endpoint will be at `http://localhost:8080/sse`.

For interactive testing with the MCP Inspector:
```bash
mcp dev server.py
```

## Deployment (Cloud Foundry — SAP BTP)

### Live URLs
- **MCP SSE endpoint:** https://tm-skills-mcp.cfapps.ap10.hana.ondemand.com/sse
- **CF org/space:** SEAIO_dial-3-0-zme762l7 / dev

### Deploy
```bash
./deploy.sh               # Reads API key from ../tm_app/.api-key automatically
```

The deploy script handles:
- `cf push --no-start` → `cf set-env TM_API_KEY` → `cf start`
- Reads the API key from `../tm_app/.api-key` (shared with the TM Skills API)
- Falls back to prompting if the file doesn't exist

### Manual deploy
```bash
cf push --no-start
cf set-env tm-skills-mcp TM_API_KEY "your-api-key"
cf start tm-skills-mcp
```

### Key CF notes
- `manifest.yml` must NOT contain `TM_API_KEY` — it would overwrite `cf set-env` on every push
- Health check is `port` type (checks if the SSE server is listening)
- CF assigns `$PORT` automatically — the server reads it from the environment
- 256M memory is sufficient for the MCP SDK + httpx stack

### Connecting from Joule Studio
Add the MCP server URL as a tool in Joule Studio:
```
https://tm-skills-mcp.cfapps.ap10.hana.ondemand.com/sse
```

## MCP Primitives

### Tools (13 — one per API endpoint)
Each tool wraps a GET endpoint. The tool name matches the business question it answers.

| Tool | Wraps Endpoint | Description |
|------|---------------|-------------|
| `get_employee_skills` | `GET /tm/employees/{id}/skills` | Full skill profile for an employee |
| `get_skill_evidence` | `GET /tm/employees/{id}/skills/{sid}/evidence` | Evidence behind a skill rating |
| `get_top_experts` | `GET /tm/skills/{id}/experts` | Top experts for a skill |
| `get_skill_coverage` | `GET /tm/skills/{id}/coverage` | Proficiency distribution for a skill |
| `search_talent` | `GET /tm/talent/search` | Multi-skill AND search |
| `get_evidence_backed_candidates` | `GET /tm/skills/{id}/candidates` | Candidates with strong evidence |
| `get_stale_skills` | `GET /tm/skills/{id}/stale` | Skills needing revalidation |
| `get_top_skills` | `GET /tm/employees/{id}/top-skills` | Employee's skill passport |
| `get_cooccurring_skills` | `GET /tm/skills/{id}/cooccurring` | Skill adjacency / co-occurrence |
| `get_evidence_inventory` | `GET /tm/employees/{id}/evidence` | All evidence for an employee |
| `browse_skills` | `GET /tm/skills` | Skill catalog with filters |
| `get_org_skill_summary` | `GET /tm/orgs/{id}/skills/summary` | Org-level skill summary |
| `get_org_skill_experts` | `GET /tm/orgs/{id}/skills/{sid}/experts` | Skill experts within an org |

### Resources (2 — static context for the LLM)
| URI | Content |
|-----|---------|
| `tm://schema` | The TM database schema DDL (`tm_schema.sql`) |
| `tm://business-questions` | Business questions catalog with API mappings |

### Prompts (3 — reusable prompt templates)
| Prompt | Purpose |
|--------|---------|
| `find_experts` | Guide the LLM to find experts for a skill |
| `analyze_employee` | Guide the LLM to build a comprehensive employee profile |
| `org_talent_review` | Guide the LLM to assess an org's talent landscape |

## TM Skills API Reference
- **Live URL:** https://tm-skills-api.cfapps.ap10.hana.ondemand.com
- **Local URL:** http://localhost:8000 (when running `../tm_app` locally)
- **Auth:** `X-API-Key` header (required when `API_KEYS` env var is set on the API)
- **Rate limit:** 60 requests/minute per client IP

### ID Formats (validated by the API)
- Employee IDs: `EMP` followed by 6 digits (e.g., `EMP000001`)
- Org IDs: `ORG` followed by 1-4 digits and optional letter (e.g., `ORG030`, `ORG031B`)
- Skill IDs: integers (e.g., `1`, `42`, `93`)
- Skill categories: `technical`, `functional`, `leadership`, `domain`, `tool`, `other`

### Key Conventions
- All endpoints are GET (read-only API)
- The API returns JSON with Pydantic-validated response shapes
- 8 of 12 endpoints expose employee PII — the API key protects access
- The skill catalog has 93 skills across 5 categories
