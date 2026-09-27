"""学生起始代码：资料已经能够加载，但检索函数尚未完成。"""

from __future__ import annotations

import json
from pathlib import Path


def load_sources(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("FAQ资料必须是列表")
    return payload


GENERIC_KEYWORDS = {"学生", "学校", "实习单位"}


def matched_keywords(question: str, item: dict) -> list[str]:
    """返回真正能区分问题主题的关键词，避免宽泛词造成误命中。"""
    normalized_question = question.strip().lower()
    return [
        keyword
        for keyword in item.get("keywords", [])
        if keyword not in GENERIC_KEYWORDS and keyword.lower() in normalized_question
    ]


def retrieve(question: str, sources: list[dict]) -> list[dict]:
    results = []
    for item in sources:
        # TODO：调用 matched_keywords；有具体关键词命中时，把 item 加入 results。
        pass
    return results


def answer(question: str, sources: list[dict]) -> dict:
    hits = retrieve(question, sources)
    if not hits:
        return {"answer": "现有资料不足，无法给出可靠结论。", "citations": [], "scope": None}
    first = hits[0]
    # TODO：把下方三个空字符串替换为 first 中对应的字段。
    return {
        "answer": first["answer"],
        "citations": [{"title": first["source_title"], "url": "", "authority": ""}],
        "scope": "",
    }


if __name__ == "__main__":
    data = load_sources(str(Path(__file__).parent / "data" / "faq.json"))
    print(answer("实习单位可以安排学生上夜班吗？", data))
