"""学生起始代码：资料已经能够加载，但检索函数尚未完成。"""

from __future__ import annotations

import json
from pathlib import Path


def load_sources(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("FAQ资料必须是列表")
    return payload


def retrieve(question: str, sources: list[dict]) -> list[dict]:
    # TODO: 学生根据问题文本、关键词和适用范围实现检索。
    # 当前故意返回空列表，用于真实诊断与分层指导验证。
    return []


def answer(question: str, sources: list[dict]) -> dict:
    hits = retrieve(question, sources)
    if not hits:
        return {"answer": "现有资料不足，无法给出可靠结论。", "citations": [], "scope": None}
    first = hits[0]
    return {
        "answer": first["answer"],
        "citations": [{"title": first["source_title"], "url": first["source"]}],
        "scope": first["scope"],
    }


if __name__ == "__main__":
    data = load_sources(str(Path(__file__).parent / "data" / "faq.json"))
    print(answer("实习单位可以安排学生上夜班吗？", data))
