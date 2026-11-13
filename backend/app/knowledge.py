"""内置知识库：领域识别 + 知识点检索（可直接替换为 Chroma / FAISS 向量检索）。"""
import json
from functools import lru_cache

from .config import DATA_DIR
from .schemas import KnowledgePoint


@lru_cache
def load_kb() -> dict:
    path = DATA_DIR / "knowledge_base.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"内置知识库加载失败（{path}）：{type(exc).__name__}: {exc}。"
            "请检查 JSON 是否被改动损坏。"
        ) from exc


def domain_label(domain: str) -> str:
    kb = load_kb()
    if domain in kb["domains"]:
        return kb["domains"][domain]["label"]
    return kb["generic"]["label"]


def detect_domain(goal: str) -> tuple[str, int]:
    """返回 (领域 key, 命中关键词数)。「Python + 后端」这类组合词优先判定。"""
    text = goal.lower().replace(" ", "")
    if "python" in text and any(
        kw in text for kw in ("后端", "服务端", "web", "接口", "fastapi", "django", "flask", "爬虫")
    ):
        return "python-backend", 3
    best, best_hits = "generic", 0
    for key, domain in load_kb()["domains"].items():
        hits = sum(1 for kw in domain["keywords"] if kw.replace(" ", "").lower() in text)
        if hits > best_hits:
            best, best_hits = key, hits
    return best, best_hits


def get_points(domain: str) -> list[KnowledgePoint]:
    kb = load_kb()
    raw = kb["domains"].get(domain, kb["generic"])["points"]
    return [KnowledgePoint(**p) for p in raw]


def overview(domain: str) -> str:
    kb = load_kb()
    return kb["domains"].get(domain, kb["generic"])["overview"]


def filter_by_keywords(points: list[KnowledgePoint], keywords: list[str]) -> list[KnowledgePoint]:
    """关键词相关的知识点，用于在 LLM 扩写时限制检索范围。"""
    if not keywords:
        return points
    hits = [
        p
        for p in points
        if any(kw.lower() in (p.name + p.summary + p.category).lower() for kw in keywords)
    ]
    return hits or points
