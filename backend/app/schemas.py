"""Agent 之间流转的数据契约。"""
from typing import Literal

from pydantic import BaseModel, Field

StageKey = Literal["beginner", "basic", "advanced", "practice"]

STAGE_META: dict[str, dict[str, str]] = {
    "beginner": {"name": "入门", "color": "#4cc9f0", "desc": "建立全局认知，补齐必备前置"},
    "basic": {"name": "基础", "color": "#7b61ff", "desc": "掌握核心概念与基本工具链"},
    "advanced": {"name": "进阶", "color": "#f7a531", "desc": "攻克难点专题，形成方法论"},
    "practice": {"name": "实战", "color": "#3ddc97", "desc": "项目驱动，产出可验证成果"},
}
STAGE_ORDER: list[str] = ["beginner", "basic", "advanced", "practice"]


class GenerateRequest(BaseModel):
    goal: str = Field(..., min_length=2, max_length=300, description="学习目标，自然语言")
    level: str = Field("零基础", description="当前基础：零基础 / 有入门基础 / 有相关经验 / 进阶提升")
    weeks: int = Field(12, ge=1, le=104, description="期望周期（周）")
    hours_per_week: int = Field(10, ge=1, le=80, description="每周可投入小时")


class IntentResult(BaseModel):
    raw_goal: str
    topic: str
    domain: str
    domain_label: str
    level: str
    target: str
    duration_weeks: int
    hours_per_week: int
    focus: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    profile: str = ""
    source: Literal["llm", "rule"] = "rule"


class KnowledgePoint(BaseModel):
    id: str
    name: str
    category: str
    level: int = Field(1, ge=1, le=4)
    summary: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    hours: int = 4
    resource: str = ""


class KnowledgeResult(BaseModel):
    topic: str
    points: list[KnowledgePoint]
    source: str = "kb"
    note: str = ""


class PathNode(BaseModel):
    id: str
    name: str
    stage: StageKey
    seq: int
    week: int
    start_week: int
    end_week: int
    hours: int
    category: str
    level: int
    summary: str = ""
    resource: str = ""
    depends_on: list[str] = Field(default_factory=list)


class Stage(BaseModel):
    key: StageKey
    name: str
    color: str
    desc: str
    start_week: int
    end_week: int
    hours: int
    objectives: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)


class WeekPlan(BaseModel):
    week: int
    stage: str
    stage_name: str
    hours: float
    focus: list[str] = Field(default_factory=list)


class Milestone(BaseModel):
    week: int
    stage: str
    title: str
    deliverable: str


class PathResult(BaseModel):
    topic: str
    total_weeks: int
    total_hours: int
    nodes: list[PathNode]
    stages: list[Stage]
    weekly_plan: list[WeekPlan]
    milestones: list[Milestone] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    severity: Literal["high", "medium", "low"]
    type: str
    message: str
    suggestion: str
    related: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    score: int = 100
    passed: bool = True
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)
    source: Literal["llm", "rule"] = "rule"


class GraphNode(BaseModel):
    id: str
    name: str
    category: str
    stage: str
    stage_name: str
    level: int
    hours: int
    week: int
    summary: str = ""
    resource: str = ""
    depends_on: list[str] = Field(default_factory=list)


class GraphLink(BaseModel):
    source: str
    target: str


class GanttItem(BaseModel):
    name: str
    stage: str
    category: str
    start_week: int
    end_week: int
    hours: int


class VisualData(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    categories: list[dict]
    stages: list[dict]
    gantt: list[GanttItem]
    stats: dict


class AgentTrace(BaseModel):
    key: str
    name: str
    role: str
    status: Literal["waiting", "running", "done", "error"]
    detail: str = ""
    elapsed_ms: int = 0


class GenerateResult(BaseModel):
    intent: IntentResult
    knowledge: KnowledgeResult
    path: PathResult
    review: ReviewResult
    visual: VisualData
    trace: list[AgentTrace]
    llm_mode: str
    elapsed_ms: int


class AdjustRequest(BaseModel):
    """用户手动调整后的重算请求：保留完整知识库，只改变纳入范围与学时。"""

    intent: IntentResult
    knowledge: KnowledgeResult
    selected_ids: list[str] = Field(..., min_length=1, description="纳入路径的知识点 id")
    weeks: int = Field(12, ge=1, le=104)
    hours_per_week: int = Field(10, ge=1, le=80)


class AdjustResult(BaseModel):
    path: PathResult
    visual: VisualData
    review: ReviewResult
    elapsed_ms: int
