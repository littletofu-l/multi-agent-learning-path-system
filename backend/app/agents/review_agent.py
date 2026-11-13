"""Review Agent：校验路径的完整性、依赖合法性与学习负荷，并给出修订建议。"""
from ..llm import llm
from ..schemas import IntentResult, KnowledgeResult, PathResult, ReviewIssue, ReviewResult

PENALTY = {"high": 18, "medium": 8, "low": 3}


def _rule_checks(intent: IntentResult, knowledge: KnowledgeResult, path: PathResult) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    kb = {p.id: p for p in knowledge.points}
    in_path = {n.id for n in path.nodes}
    seq = {n.id: n.seq for n in path.nodes}

    missing: dict[str, list[str]] = {}
    for n in path.nodes:
        point = kb.get(n.id)
        if not point:
            continue
        for dep in point.prerequisites:
            if dep not in in_path and dep in kb and kb[dep].level > 1:
                missing.setdefault(dep, []).append(n.name)
    for dep, users in missing.items():
        issues.append(
            ReviewIssue(
                severity="high",
                type="缺失前置",
                message=f"知识点「{kb[dep].name}」是「{users[0]}」等 {len(users)} 个节点的前置，但未纳入路径。",
                suggestion=f"将「{kb[dep].name}」插入到「{users[0]}」之前，保证先修关系完整。",
                related=[dep],
            )
        )

    for n in path.nodes:
        for dep in n.depends_on:
            if dep in seq and seq[dep] > n.seq:
                issues.append(
                    ReviewIssue(
                        severity="high",
                        type="顺序错误",
                        message=f"「{n.name}」排在了其前置「{kb[dep].name if dep in kb else dep}」之前。",
                        suggestion="调整排序，确保所有前置知识先于当前知识点。",
                        related=[dep, n.id],
                    )
                )

    present = {s.key for s in path.stages}
    for key, name in (("basic", "基础"), ("advanced", "进阶"), ("practice", "实战")):
        if key not in present:
            issues.append(
                ReviewIssue(
                    severity="medium" if key != "practice" else "high",
                    type="阶段缺失",
                    message=f"路径缺少「{name}」阶段，学习梯度可能不完整。",
                    suggestion=f"补充 1-3 个{name}阶段知识点，或调整知识点层级标注。",
                )
            )

    if path.total_weeks > intent.duration_weeks:
        issues.append(
            ReviewIssue(
                severity="medium",
                type="周期超限",
                message=f"按每周 {intent.hours_per_week} 小时计算需要 {path.total_weeks} 周，超出期望的 {intent.duration_weeks} 周。",
                suggestion="提高每周投入时长，或删减非核心知识点后重新规划。",
            )
        )

    heavy = [n for n in path.nodes if n.hours > intent.hours_per_week * 1.4]
    for n in heavy[:2]:
        issues.append(
            ReviewIssue(
                severity="low",
                type="单点负荷",
                message=f"「{n.name}」需要 {n.hours} 小时，超过单周投入的 1.4 倍，容易中断。",
                suggestion="拆分为 2-3 个子任务，跨周推进。",
                related=[n.id],
            )
        )

    if not any(n.level == 4 for n in path.nodes):
        issues.append(
            ReviewIssue(
                severity="medium",
                type="缺少实战",
                message="路径没有实战级节点，学完缺少可验证的产出。",
                suggestion="在末尾追加一个项目实战节点，并明确交付物。",
            )
        )

    linked = {d for n in path.nodes for d in n.depends_on} | {n.id for n in path.nodes if n.depends_on}
    isolated = [n for n in path.nodes if n.id not in linked and n.level > 1]
    if isolated and len(path.nodes) > 6:
        issues.append(
            ReviewIssue(
                severity="low",
                type="孤立知识点",
                message=f"「{isolated[0].name}」等 {len(isolated)} 个知识点缺少依赖关系，可能与其他内容脱节。",
                suggestion="补充前置关系，或与相邻知识点合并为专题。",
                related=[n.id for n in isolated[:3]],
            )
        )

    if intent.domain == "generic":
        issues.append(
            ReviewIssue(
                severity="low",
                type="知识库覆盖",
                message="该目标未命中内置领域知识库，当前路径基于通用学习范式生成。",
                suggestion="配置 LLM Key 可获得更贴合该领域的知识点，或扩充 backend/app/data/knowledge_base.json。",
            )
        )
    return issues


async def _llm_checks(intent: IntentResult, path: PathResult) -> list[ReviewIssue]:
    if not llm.enabled:
        return []
    outline = "\n".join(
        f"{n.seq}. [{n.stage}] {n.name}（{n.hours}h，第{n.start_week}-{n.end_week}周）"
        for n in path.nodes[:32]
    )
    system = (
        "你是学习路径系统的 Review Agent，负责审查路径的完整性与合理性。"
        "只输出 JSON。只提出具体、可执行的改进点，不要重复路径里已经做对的事情。"
    )
    user = (
        f"学习目标：{intent.raw_goal}\n周期：{intent.duration_weeks} 周，每周 {intent.hours_per_week} 小时\n"
        f"路径：\n{outline}\n"
        '输出 JSON：{"issues":[{"severity":"high|medium|low","type":"类型","message":"问题","suggestion":"建议"}]}，'
        "最多 3 条，没有问题就返回空数组。"
    )
    data = await llm.chat_json(system, user, temperature=0.3, max_tokens=4000)
    result: list[ReviewIssue] = []
    if not data or not isinstance(data.get("issues"), list):
        return result
    for raw in data["issues"][:3]:
        if not isinstance(raw, dict) or not raw.get("message"):
            continue
        severity = raw.get("severity") if raw.get("severity") in PENALTY else "low"
        result.append(
            ReviewIssue(
                severity=severity,
                type=str(raw.get("type") or "模型建议")[:12],
                message=str(raw["message"])[:160],
                suggestion=str(raw.get("suggestion") or "")[:160],
            )
        )
    return result


async def run(intent: IntentResult, knowledge: KnowledgeResult, path: PathResult) -> ReviewResult:
    issues = _rule_checks(intent, knowledge, path)
    llm_issues = await _llm_checks(intent, path)
    issues.extend(llm_issues)

    score = max(40, 100 - sum(PENALTY[i.severity] for i in issues))
    high = sum(1 for i in issues if i.severity == "high")
    passed = high == 0 and score >= 75

    if not issues:
        summary = "路径依赖完整、阶段梯度合理、学习负荷匹配，可直接执行。"
    else:
        summary = (
            f"共发现 {len(issues)} 个问题（严重 {high} 个），"
            f"整体质量分 {score} 分，{'建议修订后执行' if not passed else '可执行，按建议微调更佳'}。"
        )
    return ReviewResult(
        score=score,
        passed=passed,
        summary=summary,
        issues=issues,
        fixes=[],
        source="llm" if llm_issues else "rule",
    )
