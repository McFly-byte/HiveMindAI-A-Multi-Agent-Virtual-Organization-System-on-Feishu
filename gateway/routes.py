from fastapi import APIRouter, Depends, HTTPException
from agent_runtime.loop import run_agent
from agent_runtime.result import AgentResult
from agents.followup_agent import FollowUpAgent
from agents.project_secretary_agent import ProjectSecretaryAgent
from agents.risk_analysis_agent import RiskAnalysisAgent
from agents.weekly_report_agent import WeeklyReportAgent
from config.settings import get_settings
from gateway.auth import require_api_key
from gateway.heartbeat import HealthResponse, get_health
from gateway.run_lock import agent_run_lock
from schemas.base import RunRequest

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Heartbeat endpoint for service and environment health."""
    return get_health()


@router.post("/agents/project-secretary/run", response_model=AgentResult, dependencies=[Depends(require_api_key)])
async def run_project_secretary(request: RunRequest) -> AgentResult:
    return await run_agent(ProjectSecretaryAgent(max_steps=get_settings().agent_max_steps), request)


@router.post("/agents/risk-analysis/run", response_model=AgentResult, dependencies=[Depends(require_api_key)])
async def run_risk_analysis(request: RunRequest) -> AgentResult:
    return await run_agent(RiskAnalysisAgent(max_steps=get_settings().agent_max_steps), request)


@router.post("/agents/follow-up/run", response_model=AgentResult, dependencies=[Depends(require_api_key)])
async def run_follow_up(request: RunRequest) -> AgentResult:
    return await run_agent(FollowUpAgent(max_steps=get_settings().agent_max_steps), request)


@router.post("/agents/weekly-report/run", response_model=AgentResult, dependencies=[Depends(require_api_key)])
async def run_weekly_report(request: RunRequest) -> AgentResult:
    return await run_agent(WeeklyReportAgent(max_steps=get_settings().agent_max_steps), request)


@router.post("/demo/run-full-chain", dependencies=[Depends(require_api_key)])
async def run_full_chain(request: RunRequest) -> dict[str, list[AgentResult]]:
    """Run the MVP demo chain in order. Current implementation returns scaffold Agent results."""
    async with agent_run_lock(f"demo:{request.project_id or 'all'}"):
        results = [await run_project_secretary(request), await run_risk_analysis(request), await run_follow_up(request), await run_weekly_report(request)]
    failed = [result.agent_name for result in results if result.status == "failed"]
    if failed:
        raise HTTPException(status_code=500, detail={"failed_agents": failed, "results": [r.model_dump() for r in results]})
    return {"results": results}
