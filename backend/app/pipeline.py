"""多 Agent 编排：LangGraph 状态图（不可用时自动降级为顺序执行）。"""
import time
from typing import Any, AsyncIterator, Awaitable, Callable, TypedDict

from .agents import intent as intent_agent
from .agents import knowledge_agent, path_agent, review_agent, visual_agent
from .llm import llm
from .schemas import AgentTrace, GenerateRequest, GenerateResult

AGENTS = [
    ("intent", "Intent Agent", "识别学习目标与当前基础"),
    ("knowledge", "Knowledge Agent", "检索领域知识体系"),
    ("path", "Path Agent", "规划阶段化学习路径"),
    ("visual", "Visual Agent", "生成可视化结构数据"),
    ("review", "Review Agent", "检查路径完整性并给出修订建议"),
]
AGENT_NAME = {k: (n, r) for k, n, r in AGENTS}

Emit = Callable[[dict], Awaitable[None]]


class PipelineState(TypedDict, total=False):
    goal: str
    level: str
    weeks: int
    hpw: int
    intent: Any
    knowledge: Any
    path: Any
    review: Any
    visual: Any
    trace: list
    force_include: list
    applied: list
    round: int


def _build_graph(emit: Emit, traces: dict[str, AgentTrace]):
    from langgraph.graph import END, StateGraph

    async def _start(key: str) -> float:
        traces[key].status = "running"
        await emit({"type": "agent", "key": key, "status": "running"})
        return time.perf_counter()

    async def _done(key: str, detail: str, t0: float) -> None:
        traces[key].status = "done"
        traces[key].detail = detail
        traces[key].elapsed_ms = int((time.perf_counter() - t0) * 1000)
        await emit(
            {
                "type": "agent",
                "key": key,
                "status": "done",
                "detail": detail,
                "elapsed_ms": traces[key].elapsed_ms,
            }
        )

    async def intent_node(state: PipelineState):
        t0 = await _start("intent")
        result = await intent_agent.run(state["goal"], state["level"], state["weeks"], state["hpw"])
        detail = f"{result.domain_label} · {result.level} · {result.duration_weeks} 周 / 每周 {result.hours_per_week}h"
        await _done("intent", detail, t0)
        return {"intent": result, "trace": list(traces.values())}

    async def knowledge_node(state: PipelineState):
        t0 = await _start("knowledge")
        result = await knowledge_agent.run(state["intent"])
        await _done("knowledge", f"{len(result.points)} 个知识点 · 来源 {result.source}", t0)
        return {"knowledge": result, "trace": list(traces.values())}

    async def path_node(state: PipelineState):
        t0 = await _start("path")
        force = set(state.get("force_include") or [])
        result = await path_agent.run(state["intent"], state["knowledge"], force)
        applied = list(state.get("applied") or [])
        if force:
            names = {p.id: p.name for p in state["knowledge"].points}
            applied = [f"补入前置知识「{names.get(i, i)}」" for i in force] + applied
        detail = f"{len(result.nodes)} 个知识点 · {result.total_weeks} 周 · {result.total_hours} 学时"
        await _done("path", detail, t0)
        return {"path": result, "applied": applied, "trace": list(traces.values())}

    async def visual_node(state: PipelineState):
        t0 = await _start("visual")
        result = await visual_agent.run(state["path"])
        await _done("visual", f"{len(result.nodes)} 个节点 / {len(result.links)} 条依赖边", t0)
        return {"visual": result, "trace": list(traces.values())}

    async def review_node(state: PipelineState):
        t0 = await _start("review")
        result = await review_agent.run(state["intent"], state["knowledge"], state["path"])
        result.fixes = list(state.get("applied") or [])
        round_no = int(state.get("round") or 0) + 1
        fix_ids = [r for i in result.issues if i.type == "缺失前置" for r in i.related]
        fix_ids = list(dict.fromkeys(fix_ids))
        detail = f"质量分 {result.score} · {len(result.issues)} 个问题"
        if fix_ids and round_no < 2:
            detail += " · 触发修订"
        await _done("review", detail, t0)
        return {
            "review": result,
            "force_include": fix_ids if round_no < 2 else [],
            "round": round_no,
            "trace": list(traces.values()),
        }

    def after_review(state: PipelineState) -> str:
        return "path" if state.get("force_include") else END

    graph = StateGraph(PipelineState)
    graph.add_node("intent", intent_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("path", path_node)
    graph.add_node("visual", visual_node)
    graph.add_node("review", review_node)
    graph.set_entry_point("intent")
    graph.add_edge("intent", "knowledge")
    graph.add_edge("knowledge", "path")
    graph.add_edge("path", "visual")
    graph.add_edge("visual", "review")
    graph.add_conditional_edges("review", after_review, {"path": "path", END: END})
    return graph.compile()


def _init_traces() -> dict[str, AgentTrace]:
    return {
        key: AgentTrace(key=key, name=name, role=role, status="waiting")
        for key, name, role in AGENTS
    }


async def run_pipeline(req: GenerateRequest) -> AsyncIterator[dict]:
    """产出 SSE 事件：agent 进度、result 终态、error 异常。"""
    traces = _init_traces()
    queue: list[dict] = []

    async def emit(event: dict) -> None:
        queue.append(event)

    initial: PipelineState = {
        "goal": req.goal,
        "level": req.level,
        "weeks": req.weeks,
        "hpw": req.hours_per_week,
        "trace": list(traces.values()),
        "force_include": [],
        "applied": [],
        "round": 0,
    }
    t0 = time.perf_counter()

    try:
        try:
            graph = _build_graph(emit, traces)
            state: dict = dict(initial)
            async for update in graph.astream(initial, stream_mode="updates", config={"recursion_limit": 30}):
                for chunk in update.values():
                    if isinstance(chunk, dict):
                        state.update(chunk)
                for event in queue:
                    yield event
                queue.clear()
        except Exception:  # LangGraph 缺失 / 版本不兼容 / 图执行异常：降级为顺序编排
            state = await _run_sequential(initial, emit, traces)
            for event in queue:
                yield event
            queue.clear()

        final = GenerateResult(
            intent=state["intent"],
            knowledge=state["knowledge"],
            path=state["path"],
            review=state["review"],
            visual=state["visual"],
            trace=[traces[k] for k, _, _ in AGENTS],
            llm_mode=llm.mode,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        yield {"type": "result", "data": final.model_dump()}
    except Exception as exc:  # 把异常回传给前端，避免静默失败
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}


async def _run_sequential(initial: PipelineState, emit: Emit, traces: dict[str, AgentTrace]) -> dict:
    """无 LangGraph 时的等价顺序编排。"""
    state: dict = dict(initial)
    for key in ("intent", "knowledge", "path", "visual", "review"):
        traces[key].status = "running"
        await emit({"type": "agent", "key": key, "status": "running"})
        t0 = time.perf_counter()
        if key == "intent":
            state["intent"] = await intent_agent.run(state["goal"], state["level"], state["weeks"], state["hpw"])
            detail = f"{state['intent'].domain_label} · {state['intent'].level}"
        elif key == "knowledge":
            state["knowledge"] = await knowledge_agent.run(state["intent"])
            detail = f"{len(state['knowledge'].points)} 个知识点"
        elif key == "path":
            state["path"] = await path_agent.run(state["intent"], state["knowledge"])
            detail = f"{len(state['path'].nodes)} 个知识点"
        elif key == "visual":
            state["visual"] = await visual_agent.run(state["path"])
            detail = f"{len(state['visual'].nodes)} 个节点"
        else:
            state["review"] = await review_agent.run(state["intent"], state["knowledge"], state["path"])
            detail = f"质量分 {state['review'].score}"
        traces[key].status = "done"
        traces[key].detail = detail
        traces[key].elapsed_ms = int((time.perf_counter() - t0) * 1000)
        await emit(
            {
                "type": "agent",
                "key": key,
                "status": "done",
                "detail": detail,
                "elapsed_ms": traces[key].elapsed_ms,
            }
        )
    state["trace"] = list(traces.values())
    return state
