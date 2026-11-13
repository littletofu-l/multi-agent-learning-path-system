"""Knowledge Agent：检索领域知识体系，并在 LLM 可用时补充长尾知识点。"""
import hashlib

from ..knowledge import get_points, overview
from ..llm import llm
from ..schemas import IntentResult, KnowledgePoint, KnowledgeResult


def _merge(base: list[KnowledgePoint], extra: list[KnowledgePoint]) -> list[KnowledgePoint]:
    seen = {p.name for p in base}
    merged = list(base)
    for p in extra:
        if p.name in seen:
            continue
        seen.add(p.name)
        merged.append(p)
    return merged


async def run(intent: IntentResult) -> KnowledgeResult:
    base = get_points(intent.domain)
    note = overview(intent.domain)
    if not llm.enabled:
        return KnowledgeResult(topic=intent.topic, points=base, source="kb", note=note)

    existing = "、".join(p.name for p in base)
    system = (
        "你是学习路径系统的 Knowledge Agent。你熟悉各学科的知识体系与先修关系。"
        "只输出 JSON，不要解释。新增知识点必须具体、可学习、可验收，避免与已有知识点重复。"
    )
    user = (
        f"学习目标：{intent.raw_goal}\n目标主题：{intent.topic}\n领域：{intent.domain_label}\n"
        f"已有知识点：{existing}\n"
        "请补充 4-6 个该目标下重要但上面缺失的知识点（若已足够完整则返回空数组）。\n"
        '输出 JSON：{"points":[{"name":"","category":"","level":1-4,"hours":整数,'
        '"prerequisites":["已有知识点名称"],"summary":"一句话说明","resource":"推荐资源"}]}'
    )
    data = await llm.chat_json(system, user, temperature=0.4, max_tokens=5000)
    if not data or not isinstance(data.get("points"), list):
        return KnowledgeResult(topic=intent.topic, points=base, source="kb", note=note)

    by_name = {p.name: p.id for p in base}
    extra: list[KnowledgePoint] = []
    for raw in data["points"][:6]:
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            continue
        name = str(raw["name"]).strip()[:40]
        prereq_names = [str(x) for x in (raw.get("prerequisites") or [])]
        prereqs = [by_name[n] for n in prereq_names if n in by_name]
        try:
            level = max(1, min(4, int(raw.get("level", 3))))
            hours = max(1, min(30, int(raw.get("hours", 6))))
        except (TypeError, ValueError):
            level, hours = 3, 6
        # id 掺入名称哈希：同名扩展点跨次生成保持稳定，localStorage 勾选不错乱
        digest = hashlib.md5(f"{intent.domain}:{name}".encode()).hexdigest()[:6]
        extra.append(
            KnowledgePoint(
                id=f"ext-{digest}",
                name=name,
                category=str(raw.get("category") or "扩展专题")[:20],
                level=level,
                hours=hours,
                prerequisites=prereqs,
                summary=str(raw.get("summary") or "")[:120],
                resource=str(raw.get("resource") or "")[:80],
            )
        )

    merged = _merge(base, extra)
    added = len(merged) - len(base)
    source = f"kb+llm（模型补充 {added} 条）" if added else "kb+llm"
    return KnowledgeResult(topic=intent.topic, points=merged, source=source, note=note)
