"""Intent Agent：把自然语言目标解析成结构化意图（领域、基础、周期、投入）。"""
import re

from ..knowledge import detect_domain, domain_label
from ..llm import llm
from ..schemas import IntentResult

LEVEL_KEYWORDS = [
    ("进阶提升", ["进阶", "提升", "已经会", "有经验", "熟手"]),
    ("有相关经验", ["有基础", "转行", "做过", "相关经验", "学过一点"]),
    ("有入门基础", ["入门基础", "了解过", "看过教程", "有点基础"]),
    ("零基础", ["零基础", "从零", "小白", "没基础", "刚开始"]),
]

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _norm_goal(goal: str) -> str:
    text = goal.strip()
    prefixes = (
        "我想", "我要", "我准备", "帮我", "请", "想要", "希望", "计划",
        "从零", "零基础", "系统地", "系统", "学习", "学会", "学", "备考", "准备", "入门", "转行",
    )
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if text.startswith(p):
                text = text[len(p):]
                changed = True
    return text.strip("，。,. 、") or goal


def _parse_duration(goal: str) -> int | None:
    m = re.search(r"(\d+)\s*个?\s*(周|星期)", goal)
    if m:
        return max(1, min(104, int(m.group(1))))
    m = re.search(r"(\d+|[一两二三四五六七八九十])\s*个?\s*月", goal)
    if m:
        raw = m.group(1)
        num = int(raw) if raw.isdigit() else CN_NUM.get(raw, 3)
        return max(1, min(104, num * 4))
    m = re.search(r"(\d+|[一二三四五六七八九十])\s*天", goal)
    if m:
        return 1
    if "半年" in goal:
        return 24
    if "一年" in goal or "12个月" in goal:
        return 52
    return None


def _parse_hours(goal: str) -> int | None:
    m = re.search(r"每天\s*(\d+)\s*(个)?\s*小时", goal)
    if m:
        return max(1, min(80, int(m.group(1)) * 7))
    m = re.search(r"每周\s*(\d+)\s*(个)?\s*小时", goal)
    if m:
        return max(1, min(80, int(m.group(1))))
    if "全职" in goal or "脱产" in goal:
        return 40
    if "业余" in goal or "在职" in goal:
        return 8
    return None


def _rule_intent(goal: str, level: str, weeks: int, hpw: int) -> IntentResult:
    domain, hits = detect_domain(goal)
    text = goal.lower()
    detected_level = next(
        (name for name, kws in LEVEL_KEYWORDS if any(k in text for k in kws)), None
    )
    final_level = detected_level or level
    duration = _parse_duration(goal) or weeks
    hours = _parse_hours(goal) or hpw
    topic = _norm_goal(goal)
    if domain != "generic" and (len(topic) > 14 or "，" in topic):
        topic = domain_label(domain)
    if domain == "generic" and not topic:
        topic = "自定义学习主题"
    focus = [kw for kw in re.split(r"[、，,和与/\s]+", goal) if 2 <= len(kw) <= 8][:4]
    return IntentResult(
        raw_goal=goal,
        topic=topic,
        domain=domain,
        domain_label=domain_label(domain),
        level=final_level,
        target="具备独立完成真实任务的能力" if final_level != "进阶提升" else "达到可独立设计与优化方案的水平",
        duration_weeks=duration,
        hours_per_week=hours,
        focus=focus,
        keywords=[kw for kw in re.findall(r"[A-Za-z+#.]{2,}|[\u4e00-\u9fa5]{2,6}", goal)][:8],
        profile=f"{final_level}学习者，计划 {duration} 周内每周投入 {hours} 小时。",
        source="rule",
    )


async def run(goal: str, level: str, weeks: int, hpw: int) -> IntentResult:
    rule_result = _rule_intent(goal, level, weeks, hpw)
    if not llm.enabled:
        return rule_result

    system = (
        "你是学习路径系统的 Intent Agent。任务：把用户的学习目标解析成结构化意图。"
        "只输出 JSON，不要解释。domain 必须是以下之一：machine-vision、python-backend、"
        "postgraduate、deep-learning、generic。level 取值：零基础/有入门基础/有相关经验/进阶提升。"
    )
    user = (
        f"用户输入：{goal}\n用户自报基础：{level}；期望周期：{weeks} 周；每周可投入：{hpw} 小时。\n"
        "输出 JSON 字段：topic（精炼目标名，12 字以内）、domain、level、target（一句话目标）、"
        "duration_weeks（整数）、hours_per_week（整数）、focus（3-4 个关注点数组）、"
        "keywords（检索关键词数组）、profile（对学习者的一句话画像）。"
    )
    data = await llm.chat_json(system, user, temperature=0.2, max_tokens=3000)
    if not data:
        return rule_result

    domain = str(data.get("domain", "")).strip()
    if domain not in {
        "machine-vision",
        "python-backend",
        "postgraduate",
        "deep-learning",
        "generic",
    }:
        domain = rule_result.domain
    try:
        duration = int(data.get("duration_weeks") or rule_result.duration_weeks)
        hours = int(data.get("hours_per_week") or rule_result.hours_per_week)
    except (TypeError, ValueError):
        duration, hours = rule_result.duration_weeks, rule_result.hours_per_week

    return IntentResult(
        raw_goal=goal,
        topic=str(data.get("topic") or rule_result.topic)[:40],
        domain=domain,
        domain_label=domain_label(domain),
        level=str(data.get("level") or rule_result.level),
        target=str(data.get("target") or rule_result.target),
        duration_weeks=max(1, min(104, duration)),
        hours_per_week=max(1, min(80, hours)),
        focus=[str(x)[:20] for x in (data.get("focus") or rule_result.focus)][:4],
        keywords=[str(x)[:20] for x in (data.get("keywords") or rule_result.keywords)][:10],
        profile=str(data.get("profile") or rule_result.profile),
        source="llm",
    )
