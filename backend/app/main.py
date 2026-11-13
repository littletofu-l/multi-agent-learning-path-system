"""FastAPI 入口：提供生成接口与前端静态页面。"""
import json
import time
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents import path_agent, review_agent, visual_agent
from .config import STATIC_DIR, settings
from .knowledge import load_kb
from .llm import llm
from .pipeline import run_pipeline
from .schemas import AdjustRequest, AdjustResult, GenerateRequest, GenerateResult

app = FastAPI(title="基于多 Agent 的学习路径可视化系统", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    kb = load_kb()
    ready = await llm.available()
    return {
        "status": "ok",
        "llm_enabled": ready,
        "llm_mode": llm.mode,
        "llm_model": llm.model,
        "llm_base_url": settings.llm_base_url if ready else "",
        "domains": [
            {"key": key, "label": value["label"], "count": len(value["points"])}
            for key, value in kb["domains"].items()
        ] + [{"key": "generic", "label": kb["generic"]["label"], "count": len(kb["generic"]["points"])}],
    }


@app.post("/api/generate", response_model=GenerateResult)
async def generate(req: GenerateRequest) -> GenerateResult:
    async for event in run_pipeline(req):
        if event["type"] == "result":
            return GenerateResult(**event["data"])
        if event["type"] == "error":
            raise HTTPException(status_code=500, detail=event["message"])
    raise HTTPException(status_code=500, detail="流程未产出结果")


@app.post("/api/adjust", response_model=AdjustResult)
async def adjust(req: AdjustRequest) -> AdjustResult:
    """用户手动增删知识点或调整学时后，重新排期并再次审查。"""
    known_ids = {p.id for p in req.knowledge.points}
    valid_ids = [i for i in req.selected_ids if i in known_ids]
    if not valid_ids:
        raise HTTPException(
            status_code=422,
            detail="selected_ids 与知识点集合无交集，请至少选择一个有效知识点",
        )
    started = time.perf_counter()
    intent = req.intent.model_copy(
        update={"duration_weeks": req.weeks, "hours_per_week": req.hours_per_week}
    )
    try:
        path = await path_agent.run(
            intent, req.knowledge, selected_ids=valid_ids, skip_beginner=False
        )
        visual = await visual_agent.run(path)
        review = await review_agent.run(intent, req.knowledge, path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return AdjustResult(
        path=path,
        visual=visual,
        review=review,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


@app.post("/api/generate/stream")
async def generate_stream(req: GenerateRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        async for event in run_pipeline(req):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
