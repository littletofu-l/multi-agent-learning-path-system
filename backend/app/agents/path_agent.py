"""Path Agent：按先修关系拓扑排序，划分四阶段并排入周计划。"""
import math
from collections import defaultdict

from ..schemas import (
    STAGE_META,
    STAGE_ORDER,
    IntentResult,
    KnowledgePoint,
    KnowledgeResult,
    Milestone,
    PathNode,
    PathResult,
    Stage,
    WeekPlan,
)

STAGE_BY_LEVEL = {1: "beginner", 2: "basic", 3: "advanced", 4: "practice"}
DELIVERABLES = {
    "beginner": "整理一份基础笔记 + 跑通最小可运行示例",
    "basic": "完成 2-3 个核心技能练习，形成可复用的操作清单",
    "advanced": "攻克进阶专题，输出方法论总结与踩坑记录",
    "practice": "交付一个可展示的完整项目 + 复盘文档",
}
ADVANCED_LEVELS = {"有相关经验", "进阶提升"}


def _toposort(points: list[KnowledgePoint]) -> list[KnowledgePoint]:
    by_id = {p.id: p for p in points}
    indeg = {p.id: 0 for p in points}
    children: dict[str, list[str]] = defaultdict(list)
    for p in points:
        for dep in p.prerequisites:
            if dep in by_id:
                indeg[p.id] += 1
                children[dep].append(p.id)

    order_index = {p.id: i for i, p in enumerate(points)}
    ready = sorted(
        [p for p in points if indeg[p.id] == 0],
        key=lambda p: (p.level, order_index[p.id], p.hours),
    )
    ordered: list[KnowledgePoint] = []
    while ready:
        cur = ready.pop(0)
        ordered.append(cur)
        for child in children[cur.id]:
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(by_id[child])
        ready.sort(key=lambda p: (p.level, order_index[p.id], p.hours))

    if len(ordered) < len(points):  # 存在环时兜底：按层级顺序补齐
        ordered_ids = {p.id for p in ordered}
        ordered.extend(sorted((p for p in points if p.id not in ordered_ids), key=lambda p: p.level))
    return ordered


def _select(points: list[KnowledgePoint], intent: IntentResult, force: set[str]) -> tuple[list[KnowledgePoint], list[str]]:
    if intent.level not in ADVANCED_LEVELS:
        return points, []
    kept, skipped = [], []
    for p in points:
        if p.level == 1 and p.id not in force and p.category not in {"项目实战", "目标定义", "规划"}:
            skipped.append(p.name)
        else:
            kept.append(p)
    if len(kept) < 6:  # 避免跳过过多导致路径过短
        return points, []
    return kept, skipped


def _stage_objectives(nodes: list[PathNode], stage_key: str) -> list[str]:
    names = [n.name for n in nodes][:4]
    base = {
        "beginner": "建立领域全局认知",
        "basic": "掌握核心概念与工具链",
        "advanced": "突破关键难点专题",
        "practice": "完成可展示的真实项目",
    }[stage_key]
    objectives = [base]
    if names:
        objectives.append("重点学习：" + "、".join(names))
    return objectives


async def run(
    intent: IntentResult,
    knowledge: KnowledgeResult,
    force_include: set[str] | None = None,
    selected_ids: list[str] | None = None,
    skip_beginner: bool = True,
) -> PathResult:
    force = force_include or set()
    if selected_ids is not None:
        chosen = set(selected_ids)
        kept = [p for p in knowledge.points if p.id in chosen]
        skipped = [p.name for p in knowledge.points if p.id not in chosen]
    elif skip_beginner:
        kept, skipped = _select(knowledge.points, intent, force)
    else:
        kept, skipped = knowledge.points, []
    ordered = _toposort(kept)
    kept_ids = {p.id for p in ordered}

    total_hours = sum(p.hours for p in ordered)
    capacity = max(1, intent.hours_per_week)
    needed_weeks = math.ceil(total_hours / capacity) if total_hours else 0
    total_weeks = max(1, intent.duration_weeks, needed_weeks)
    pace = total_hours / total_weeks if total_hours else 1

    nodes: list[PathNode] = []
    assigned: dict[str, PathNode] = {}
    cum = 0
    for seq, p in enumerate(ordered, start=1):
        deps = [d for d in p.prerequisites if d in kept_ids]
        start = int(cum // pace) + 1
        end = max(start, math.ceil((cum + p.hours) / pace))
        if deps:
            start = max(start, max(assigned[d].start_week for d in deps))
            end = max(end, start)
        start = min(start, total_weeks)
        end = min(max(end, start), total_weeks)
        node = PathNode(
            id=p.id,
            name=p.name,
            stage=STAGE_BY_LEVEL.get(p.level, "basic"),  # type: ignore[arg-type]
            seq=seq,
            week=start,
            start_week=start,
            end_week=end,
            hours=p.hours,
            category=p.category,
            level=p.level,
            summary=p.summary,
            resource=p.resource,
            depends_on=deps,
        )
        nodes.append(node)
        assigned[p.id] = node
        cum += p.hours

    stages: list[Stage] = []
    for key in STAGE_ORDER:
        stage_nodes = [n for n in nodes if n.stage == key]
        if not stage_nodes:
            continue
        meta = STAGE_META[key]
        stages.append(
            Stage(
                key=key,  # type: ignore[arg-type]
                name=meta["name"],
                color=meta["color"],
                desc=meta["desc"],
                start_week=min(n.start_week for n in stage_nodes),
                end_week=max(n.end_week for n in stage_nodes),
                hours=sum(n.hours for n in stage_nodes),
                objectives=_stage_objectives(stage_nodes, key),
                node_ids=[n.id for n in stage_nodes],
            )
        )

    week_hours: dict[int, float] = defaultdict(float)
    week_nodes: dict[int, list[PathNode]] = defaultdict(list)
    for n in nodes:
        span = max(1, n.end_week - n.start_week + 1)
        for w in range(n.start_week, n.end_week + 1):
            week_hours[w] += n.hours / span
            week_nodes[w].append(n)

    weekly_plan: list[WeekPlan] = []
    for w in range(1, total_weeks + 1):
        ns = week_nodes.get(w, [])
        if ns:
            stage_counts: dict[str, int] = defaultdict(int)
            for n in ns:
                stage_counts[n.stage] += n.hours
            dominant = max(stage_counts.items(), key=lambda kv: kv[1])[0]
            stage_name = STAGE_META[dominant]["name"]
            focus = [n.name for n in ns][:3] + ([f"等 {len(ns)} 个知识点"] if len(ns) > 3 else [])
        else:
            dominant, stage_name = "buffer", "缓冲 / 复盘"
            focus = ["弹性周：补齐欠账、错题复盘与项目推进"]
        weekly_plan.append(
            WeekPlan(
                week=w,
                stage=dominant,
                stage_name=stage_name,
                hours=round(week_hours.get(w, 0), 1),
                focus=focus,
            )
        )

    milestones = [
        Milestone(
            week=s.end_week,
            stage=s.name,
            title=f"{s.name}阶段完成",
            deliverable=DELIVERABLES[s.key],
        )
        for s in stages
    ]

    rationale = [f"按先修关系拓扑排序，共纳入 {len(nodes)} 个知识点，累计 {total_hours} 学时。"]
    if selected_ids is not None and skipped:
        rationale.append(f"按你的手动调整，移出 {len(skipped)} 个知识点：{'、'.join(skipped[:4])}…")
    elif skipped:
        rationale.append(f"结合你的现有基础，跳过 {len(skipped)} 个入门级知识点：{'、'.join(skipped[:4])}…")
    rationale.append(f"按每周 {pace:.1f} 小时的节奏，排入 {total_weeks} 周。")
    if total_weeks > intent.duration_weeks:
        rationale.append(
            f"目标周期 {intent.duration_weeks} 周内无法覆盖全部内容，已自动延长至 {total_weeks} 周，"
            f"或可把每周投入提升到 {math.ceil(total_hours / intent.duration_weeks)} 小时。"
        )
    if force:
        rationale.append(f"根据 Review Agent 建议，补入了 {len(force)} 个缺失的前置知识点。")

    return PathResult(
        topic=knowledge.topic,
        total_weeks=total_weeks,
        total_hours=total_hours,
        nodes=nodes,
        stages=stages,
        weekly_plan=weekly_plan,
        milestones=milestones,
        skipped=skipped,
        rationale=rationale,
    )
