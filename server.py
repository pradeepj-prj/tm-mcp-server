"""TM Skills MCP Server — exposes the Talent Management API as MCP tools, resources, and prompts."""

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

import httpx
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from audit import AuditLogger
from config import settings

# ---------------------------------------------------------------------------
# Audit logger — module-level so MCP tools and REST endpoints share it
# ---------------------------------------------------------------------------

audit_logger = AuditLogger(settings.audit_db_path)


@asynccontextmanager
async def audit_lifespan(app: FastMCP) -> AsyncIterator[dict]:
    await audit_logger.initialize()
    yield {}
    await audit_logger.close()


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "tm-skills",
    instructions=(
        "You have access to a Talent Management Skills API that stores employee "
        "skill profiles, proficiency scores, evidence, and org hierarchy data. "
        "Use the tools to answer HR and talent questions. Employee IDs look like "
        "EMP000001. Org IDs look like ORG030. Skill IDs are numeric (e.g. 1 or 1.0). "
        "Start by browsing the skill catalog (browse_skills) if you need to find "
        "skill IDs by name."
    ),
    host=settings.host,
    port=settings.port,
    lifespan=audit_lifespan,
)

RESOURCES_DIR = Path(__file__).parent / "resources"


# ---------------------------------------------------------------------------
# @audited decorator — wraps tool functions with audit logging
# ---------------------------------------------------------------------------


def audited(fn):
    """Decorator that logs tool invocations to the audit database.

    Extracts session/client metadata from the MCP Context, measures duration,
    and records success/failure. Never lets audit errors propagate to callers.
    """

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        success = True
        error_msg = None

        # Extract context metadata (graceful degradation if unavailable)
        request_id = None
        session_id = None
        client_name = None
        client_version = None
        try:
            ctx: Context | None = kwargs.get("ctx")
            if ctx:
                # Session ID from the MCP session
                try:
                    session_id = ctx.session.client_params.meta.sessionId
                except Exception:
                    pass
                # Client info from MCP handshake
                try:
                    client_info = ctx.session.client_params.clientInfo
                    client_name = client_info.name
                    client_version = client_info.version
                except Exception:
                    pass
                # Request ID from the current JSON-RPC message
                try:
                    request_id = str(ctx.request_id)
                except Exception:
                    pass
        except Exception:
            pass

        # Build parameter dict (exclude ctx)
        params = {k: v for k, v in kwargs.items() if k != "ctx"}

        try:
            result = await fn(*args, **kwargs)
            return result
        except Exception as exc:
            success = False
            error_msg = str(exc)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            try:
                await audit_logger.log_tool_call(
                    tool_name=fn.__name__,
                    parameters=params or None,
                    success=success,
                    error_msg=error_msg,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    session_id=session_id,
                    client_name=client_name,
                    client_version=client_version,
                )
            except Exception:
                pass  # never let audit errors propagate

    return wrapper


# ---------------------------------------------------------------------------
# HTTP client helper
# ---------------------------------------------------------------------------


async def _api_get(path: str, params: dict | None = None) -> str:
    """Make a GET request to the TM Skills API and return the JSON response as a string."""
    headers = {}
    if settings.tm_api_key:
        headers["X-API-Key"] = settings.tm_api_key

    async with httpx.AsyncClient(
        base_url=settings.tm_api_base_url,
        timeout=settings.tm_api_timeout,
    ) as client:
        response = await client.get(path, params=params, headers=headers)
        response.raise_for_status()
        return response.text


# ===========================================================================
# RESOURCES — static context for the LLM
# ===========================================================================


@mcp.resource("tm://schema")
def get_schema() -> str:
    """The TM database schema — tables, columns, types, indexes, and relationships."""
    return (RESOURCES_DIR / "tm_schema.sql").read_text()


@mcp.resource("tm://business-questions")
def get_business_questions() -> str:
    """Catalog of 12 business questions the TM Skills API can answer, with endpoint mappings."""
    return (RESOURCES_DIR / "business_questions.md").read_text()


# ===========================================================================
# TOOLS — one per API endpoint
# ===========================================================================

# --- Employee-centric tools (Endpoints 1, 2, 8, 10) ---


@mcp.tool()
@audited
async def get_employee_skills(employee_id: str, ctx: Context = None) -> str:
    """Get the full skill profile for an employee — all skills with proficiency (0-5),
    confidence (0-100), source, and last updated date.

    Args:
        employee_id: Employee ID in format EMP followed by 6 digits (e.g. EMP000001)
    """
    return await _api_get(f"/tm/employees/{employee_id}/skills")


@mcp.tool()
@audited
async def get_skill_evidence(employee_id: str, skill_id: float, ctx: Context = None) -> str:
    """Get the evidence behind an employee's skill rating — certifications, projects,
    assessments, peer endorsements, etc.

    Args:
        employee_id: Employee ID (e.g. EMP000001)
        skill_id: Numeric skill ID (use browse_skills to find IDs by name)
    """
    return await _api_get(f"/tm/employees/{employee_id}/skills/{int(skill_id)}/evidence")


@mcp.tool()
@audited
async def get_top_skills(employee_id: str, limit: float = 10, ctx: Context = None) -> str:
    """Get an employee's strongest skills ranked by proficiency and confidence —
    a "skill passport" view.

    Args:
        employee_id: Employee ID (e.g. EMP000001)
        limit: Number of top skills to return (1-50, default 10)
    """
    return await _api_get(
        f"/tm/employees/{employee_id}/top-skills",
        params={"limit": int(limit)},
    )


@mcp.tool()
@audited
async def get_evidence_inventory(employee_id: str, ctx: Context = None) -> str:
    """Get ALL evidence items across ALL skills for an employee — the complete
    evidence inventory (certifications, projects, endorsements).

    Args:
        employee_id: Employee ID (e.g. EMP000001)
    """
    return await _api_get(f"/tm/employees/{employee_id}/evidence")


# --- Skill-centric tools (Endpoints 3, 4, 6, 7, 9, 11) ---


@mcp.tool()
@audited
async def browse_skills(
    category: str | None = None,
    search: str | None = None,
    ctx: Context = None,
) -> str:
    """Browse the skill catalog — list all skills or filter by category/search term.
    Use this to find skill IDs before calling other tools.

    Args:
        category: Filter by category (technical, functional, leadership, domain, tool, other)
        search: Search skill name or description (case-insensitive, max 200 chars)
    """
    params = {}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    return await _api_get("/tm/skills", params=params)


@mcp.tool()
@audited
async def get_top_experts(
    skill_id: float,
    min_proficiency: float = 4,
    limit: float = 20,
    ctx: Context = None,
) -> str:
    """Find the top experts for a specific skill — ranked by proficiency, confidence, and recency.

    Args:
        skill_id: Numeric skill ID (use browse_skills to find IDs)
        min_proficiency: Minimum proficiency level 0-5 (default 4)
        limit: Max results to return 1-100 (default 20)
    """
    return await _api_get(
        f"/tm/skills/{int(skill_id)}/experts",
        params={"min_proficiency": int(min_proficiency), "limit": int(limit)},
    )


@mcp.tool()
@audited
async def get_skill_coverage(
    skill_id: float,
    min_proficiency: float = 3,
    ctx: Context = None,
) -> str:
    """Get the proficiency distribution for a skill — how many employees at each level (0-5)
    and total count above a threshold.

    Args:
        skill_id: Numeric skill ID
        min_proficiency: Threshold for the coverage count 0-5 (default 3)
    """
    return await _api_get(
        f"/tm/skills/{int(skill_id)}/coverage",
        params={"min_proficiency": int(min_proficiency)},
    )


@mcp.tool()
@audited
async def get_evidence_backed_candidates(
    skill_id: float,
    min_proficiency: float = 3,
    min_evidence_strength: float = 4,
    limit: float = 20,
    ctx: Context = None,
) -> str:
    """Find employees with a skill AND strong evidence to back it up — certifications,
    project work, assessments with high signal strength.

    Args:
        skill_id: Numeric skill ID
        min_proficiency: Minimum proficiency level 0-5 (default 3)
        min_evidence_strength: Minimum evidence signal strength 1-5 (default 4)
        limit: Max candidates to return 1-100 (default 20)
    """
    return await _api_get(
        f"/tm/skills/{int(skill_id)}/candidates",
        params={
            "min_proficiency": int(min_proficiency),
            "min_evidence_strength": int(min_evidence_strength),
            "limit": int(limit),
        },
    )


@mcp.tool()
@audited
async def get_stale_skills(
    skill_id: float,
    older_than_days: float = 365,
    ctx: Context = None,
) -> str:
    """Find employees whose skill record hasn't been validated or updated recently —
    useful for governance and freshness checks.

    Args:
        skill_id: Numeric skill ID
        older_than_days: Skills not updated in this many days (default 365)
    """
    return await _api_get(
        f"/tm/skills/{int(skill_id)}/stale",
        params={"older_than_days": int(older_than_days)},
    )


@mcp.tool()
@audited
async def get_cooccurring_skills(
    skill_id: float,
    min_proficiency: float = 3,
    top: float = 20,
    ctx: Context = None,
) -> str:
    """Discover which skills commonly co-occur with a given skill — "people who know X
    also tend to know Y". Useful for recommendations and skill adjacency analysis.

    Args:
        skill_id: Numeric skill ID
        min_proficiency: Minimum proficiency to consider 0-5 (default 3)
        top: Number of co-occurring skills to return 1-50 (default 20)
    """
    return await _api_get(
        f"/tm/skills/{int(skill_id)}/cooccurring",
        params={"min_proficiency": int(min_proficiency), "top": int(top)},
    )


# --- Talent search tool (Endpoint 5) ---


@mcp.tool()
@audited
async def search_talent(
    skills: str,
    min_proficiency: float = 3,
    ctx: Context = None,
) -> str:
    """Find employees who have ALL specified skills at a minimum proficiency — an AND search.
    Returns matching employees with per-skill detail.

    Args:
        skills: Comma-separated skill names (e.g. "Python,SQL,Docker") — max 10 skills
        min_proficiency: Minimum proficiency for each skill 0-5 (default 3)
    """
    return await _api_get(
        "/tm/talent/search",
        params={"skills": skills, "min_proficiency": int(min_proficiency)},
    )


# --- Org-centric tools (Endpoint 12) ---


@mcp.tool()
@audited
async def get_org_skill_summary(
    org_unit_id: str,
    limit: float = 20,
    ctx: Context = None,
) -> str:
    """Get the top skills in an org unit (including all child orgs in the hierarchy) —
    aggregate counts and top experts per skill.

    Args:
        org_unit_id: Org unit ID (e.g. ORG030, ORG031B)
        limit: Number of top skills to return 1-100 (default 20)
    """
    return await _api_get(
        f"/tm/orgs/{org_unit_id}/skills/summary",
        params={"limit": int(limit)},
    )


@mcp.tool()
@audited
async def get_org_skill_experts(
    org_unit_id: str,
    skill_id: float,
    min_proficiency: float = 3,
    limit: float = 20,
    ctx: Context = None,
) -> str:
    """Find employees within an org unit who have a specific skill — scoped to the
    org hierarchy (includes child orgs).

    Args:
        org_unit_id: Org unit ID (e.g. ORG030, ORG031B)
        skill_id: Numeric skill ID
        min_proficiency: Minimum proficiency level 0-5 (default 3)
        limit: Max results 1-100 (default 20)
    """
    return await _api_get(
        f"/tm/orgs/{org_unit_id}/skills/{int(skill_id)}/experts",
        params={"min_proficiency": int(min_proficiency), "limit": int(limit)},
    )


# ===========================================================================
# AUDIT TOOLS — MCP tools for querying audit data (NOT audited themselves)
# ===========================================================================


@mcp.tool()
async def audit_get_recent_calls(limit: float = 50) -> str:
    """Get the most recent MCP tool invocations from the audit log.

    Args:
        limit: Number of recent calls to return (1-500, default 50)
    """
    rows = await audit_logger.query_recent(limit=int(limit))
    return json.dumps(rows, indent=2)


@mcp.tool()
async def audit_query_calls(
    tool_name: str | None = None,
    session_id: str | None = None,
    client_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
    errors_only: bool = False,
    limit: float = 100,
) -> str:
    """Query the audit log with filters — find calls by tool, session, client, or time range.

    Args:
        tool_name: Filter by tool name (e.g. "get_employee_skills")
        session_id: Filter by MCP session ID
        client_name: Filter by client name from MCP handshake
        since: Start of time range (ISO 8601, e.g. "2026-02-01")
        until: End of time range (ISO 8601, e.g. "2026-02-28")
        errors_only: If true, only return failed calls
        limit: Max results to return (1-500, default 100)
    """
    rows = await audit_logger.query_with_filters(
        tool_name=tool_name,
        session_id=session_id,
        client_name=client_name,
        since=since,
        until=until,
        errors_only=errors_only,
        limit=int(limit),
    )
    return json.dumps(rows, indent=2)


@mcp.tool()
async def audit_get_summary() -> str:
    """Get aggregate audit statistics — total calls, unique tools/clients, error rates,
    and per-tool duration averages.
    """
    stats = await audit_logger.get_summary_stats()
    return json.dumps(stats, indent=2)


# ===========================================================================
# AUDIT REST ENDPOINTS — plain HTTP access for visualization / curl
# ===========================================================================


@mcp.custom_route("/audit/recent", methods=["GET"])
async def audit_recent_http(request: Request) -> JSONResponse:
    limit = int(request.query_params.get("limit", "50"))
    rows = await audit_logger.query_recent(limit=limit)
    return JSONResponse(rows)


@mcp.custom_route("/audit/query", methods=["GET"])
async def audit_query_http(request: Request) -> JSONResponse:
    rows = await audit_logger.query_with_filters(
        tool_name=request.query_params.get("tool_name"),
        session_id=request.query_params.get("session_id"),
        client_name=request.query_params.get("client_name"),
        since=request.query_params.get("since"),
        until=request.query_params.get("until"),
        errors_only=request.query_params.get("errors_only", "").lower() == "true",
        limit=int(request.query_params.get("limit", "100")),
    )
    return JSONResponse(rows)


@mcp.custom_route("/audit/summary", methods=["GET"])
async def audit_summary_http(request: Request) -> JSONResponse:
    stats = await audit_logger.get_summary_stats()
    return JSONResponse(stats)


# ===========================================================================
# PROMPTS — reusable prompt templates
# ===========================================================================


@mcp.prompt()
def find_experts(skill_name: str) -> str:
    """Guide the assistant to find experts for a given skill.

    Args:
        skill_name: The name of the skill to search for (e.g. "Python", "Project Management")
    """
    return (
        f'I need to find the top experts in "{skill_name}" in our organization.\n\n'
        f"Please:\n"
        f'1. Use browse_skills to find the skill ID for "{skill_name}"\n'
        f"2. Use get_top_experts with that skill ID to find the best people\n"
        f"3. For the top 3 experts, use get_skill_evidence to show what backs up their rating\n"
        f"4. Summarize the findings: who are the go-to people and why"
    )


@mcp.prompt()
def analyze_employee(employee_id: str) -> str:
    """Build a comprehensive talent profile for an employee.

    Args:
        employee_id: Employee ID (e.g. EMP000001)
    """
    return (
        f"Please build a comprehensive talent profile for employee {employee_id}.\n\n"
        f"Steps:\n"
        f"1. Use get_employee_skills to see their full skill profile\n"
        f"2. Use get_top_skills to identify their strongest areas\n"
        f"3. Use get_evidence_inventory to see all supporting evidence\n"
        f"4. For their top 3 skills, use get_cooccurring_skills to suggest related skills they might develop\n"
        f"5. Summarize: strengths, areas backed by strong evidence, and development suggestions"
    )


@mcp.prompt()
def org_talent_review(org_unit_id: str) -> str:
    """Assess an organization's talent landscape.

    Args:
        org_unit_id: Org unit ID (e.g. ORG030)
    """
    return (
        f"Please perform a talent review for org unit {org_unit_id}.\n\n"
        f"Steps:\n"
        f"1. Use get_org_skill_summary to see the top skills in this org\n"
        f"2. For the top 3 skills, use get_skill_coverage to understand the depth\n"
        f"3. For the top 3 skills, check get_stale_skills to find outdated records\n"
        f"4. Summarize: what this org is strong in, where the gaps might be, "
        f"and any governance concerns (stale skills needing revalidation)"
    )


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins.split(","),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
