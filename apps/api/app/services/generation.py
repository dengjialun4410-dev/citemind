import re

import httpx

from ..config import Settings
from .retrieval import SearchHit, _terms, expand_query


def _best_excerpt(question: str, hit: SearchHit) -> str:
    query_terms = _terms(expand_query(question))
    candidates = [
        value.strip()
        for value in re.split(r"(?<=[。！？.!?])\s+|\n{2,}", hit.chunk.content)
        if 24 <= len(value.strip()) <= 700
    ]
    if not candidates:
        return hit.chunk.content[:420].strip()

    def score(sentence: str) -> float:
        sentence_terms = _terms(sentence)
        overlap = len(query_terms & sentence_terms) / max(1, len(query_terms))
        research_bonus = 0.0
        lowered = sentence.lower()
        if any(term in lowered for term in ("propose", "contribution", "dataset", "metric", "limitation", "future work", "outperform")):
            research_bonus = 0.08
        reference_penalty = 0.12 if lowered.startswith("references") or len(re.findall(r"\b\d{4}\b", sentence)) >= 4 else 0.0
        return overlap + research_bonus - reference_penalty

    return max(candidates, key=score)[:420].strip()


def _local_answer(question: str, hits: list[SearchHit]) -> str:
    if not hits or hits[0].score < 0.02:
        return "当前知识库中没有找到足够相关的证据。请换一种问法，或上传包含该信息的文档。"

    if any(term in question for term in ("局限", "不足", "缺点", "未来工作")):
        explicit = [
            hit
            for hit in hits
            if any(term in hit.chunk.content.lower() for term in ("limitation", "limitations", "future work", "drawback"))
        ]
        if not explicit:
            return "当前检索范围内没有找到作者明确陈述的局限性或未来工作，因此不能可靠地替作者推断。建议确认是否上传了完整论文，或切换到包含 Discussion/Conclusion 的文档。"

    sentences: list[str] = []
    seen: set[str] = set()
    dataset_intent = any(term in question for term in ("数据集", "评价指标", "评估指标", "实验指标"))
    for number, hit in enumerate(hits[:3], start=1):
        if dataset_intent:
            lines = [
                line.strip()
                for line in hit.chunk.content.splitlines()
                if any(signal in line.lower() for signal in ("dataset", "ntu", "kinetics", "top-1", "top-5", "metric", "accuracy"))
            ]
            excerpt = " ".join(lines[:5])[:420].strip() or _best_excerpt(question, hit)
        else:
            excerpt = _best_excerpt(question, hit)
        if excerpt and excerpt not in seen:
            seen.add(excerpt)
            sentences.append(f"{excerpt} [{number}]")

    heading = "文档中与数据集和评价指标直接相关的证据：" if dataset_intent else "根据知识库中检索到的内容："
    return heading + "\n\n" + "\n\n".join(sentences) + "\n\n以上回答来自本地检索摘要；配置模型密钥后可获得综合推理回答。"


async def generate_answer(question: str, hits: list[SearchHit], settings: Settings) -> tuple[str, str]:
    if not settings.openai_api_key:
        return _local_answer(question, hits), "local-extractive"

    context = "\n\n".join(
        f"[证据 {index}] 文件：{hit.document_name}，页码：{hit.chunk.page_number}\n{hit.chunk.content}"
        for index, hit in enumerate(hits, start=1)
    )
    prompt = f"""你是严谨的科研知识库助手。只根据给定证据回答问题。
要求：
1. 每个关键结论后用 [证据编号] 标注来源；
2. 证据不足时明确说明，不得编造；
3. 使用与用户问题相同的语言；
4. 回答简洁但完整。

用户问题：{question}

证据：
{context}
"""
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    payload = {
        "model": settings.openai_chat_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload
        )
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"], "remote-llm"
