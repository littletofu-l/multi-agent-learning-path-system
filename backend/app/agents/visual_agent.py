"""Visual Agent：把路径转换成前端可直接渲染的图数据、阶段数据与甘特数据。"""
from collections import Counter

from ..schemas import GanttItem, GraphLink, GraphNode, PathResult, VisualData

PALETTE = [
    "#4cc9f0", "#7b61ff", "#f7a531", "#3ddc97", "#ff6b9d",
    "#ffd166", "#06d6a0", "#ef476f", "#118ab2", "#b388ff",
]


async def run(path: PathResult) -> VisualData:
    categories = list(dict.fromkeys(n.category for n in path.nodes))
    color_of = {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(categories)}

    nodes = [
        GraphNode(
            id=n.id,
            name=n.name,
            category=n.category,
            stage=n.stage,
            stage_name=path_stage_name(path, n.stage),
            level=n.level,
            hours=n.hours,
            week=n.week,
            summary=n.summary,
            resource=n.resource,
            depends_on=n.depends_on,
        )
        for n in path.nodes
    ]
    links = [GraphLink(source=d, target=n.id) for n in path.nodes for d in n.depends_on]
    gantt = [
        GanttItem(
            name=n.name,
            stage=n.stage,
            category=n.category,
            start_week=n.start_week,
            end_week=n.end_week,
            hours=n.hours,
        )
        for n in path.nodes
    ]
    stages = [
        {
            "key": s.key,
            "name": s.name,
            "color": s.color,
            "desc": s.desc,
            "start_week": s.start_week,
            "end_week": s.end_week,
            "hours": s.hours,
            "count": len(s.node_ids),
            "objectives": s.objectives,
        }
        for s in path.stages
    ]
    level_counter = Counter(n.level for n in path.nodes)
    stats = {
        "points": len(path.nodes),
        "hours": path.total_hours,
        "weeks": path.total_weeks,
        "stages": len(path.stages),
        "links": len(links),
        "categories": [{"name": c, "color": color_of[c]} for c in categories],
        "level_dist": [{"level": lv, "count": level_counter.get(lv, 0)} for lv in (1, 2, 3, 4)],
    }
    return VisualData(
        nodes=nodes,
        links=links,
        categories=stats["categories"],
        stages=stages,
        gantt=gantt,
        stats=stats,
    )


def path_stage_name(path: PathResult, stage: str) -> str:
    for s in path.stages:
        if s.key == stage:
            return s.name
    return stage
