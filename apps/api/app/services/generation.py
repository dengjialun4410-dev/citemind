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
        method_bonus = 0.0
        if any(term in question for term in ("方法", "模型", "模块", "架构", "结构", "怎么做")):
            method_signals = ("architecture", "component", "branch", "module", "block", "gate", "framework", "pipeline")
            method_bonus = min(0.42, sum(0.07 for signal in method_signals if signal in lowered))
        reference_penalty = 0.35 if lowered.startswith("references") or len(re.findall(r"\b\d{4}\b", sentence)) >= 4 or "arxiv preprint" in lowered else 0.0
        return overlap + research_bonus + method_bonus - reference_penalty

    return max(candidates, key=score)[:420].strip()


def _dataset_answer(hits: list[SearchHit]) -> str:
    evidence = "\n".join(hit.chunk.content for hit in hits[:5])
    lowered = evidence.lower()
    datasets = [
        label
        for label, signals in (
            ("NTU RGB+D 60（NTU-60）", ("ntu rgb+d 60", "ntu-60")),
            ("NTU RGB+D 120（NTU-120）", ("ntu rgb+d 120", "ntu-120")),
            ("Kinetics-Skeleton", ("kinetics-skeleton",)),
        )
        if any(signal in lowered for signal in signals)
    ]
    metrics = [label for label, signal in (("Top-1 Accuracy", "top-1"), ("Top-5 Accuracy", "top-5")) if signal in lowered]
    if "accuracy" in lowered and not metrics:
        metrics.append("分类准确率（Accuracy）")

    result_lines: list[str] = []
    for hit in hits[:3]:
        lines = [re.sub(r"\s+", " ", line).strip() for line in hit.chunk.content.splitlines() if line.strip()]
        for index, compact in enumerate(lines):
            if len(compact) > 24 and any(signal in compact.lower() for signal in ("ours", "td-gcn", "achieves", "obtains")):
                start = max(0, index - 1)
                result_lines.append(" ".join(lines[start : index + 4])[:420])
                break
        if result_lines:
            break

    sections = [
        "数据集\n" + ("\n".join(f"- {item}" for item in datasets) if datasets else "- 当前证据未明确列出数据集名称"),
        "评价指标\n" + ("\n".join(f"- {item}" for item in metrics) if metrics else "- 当前证据未明确列出评价指标"),
    ]
    if result_lines:
        sections.append(f"论文报告的结果\n- {result_lines[0]} [1]")
    sections.append("关键结论请结合下方页码引用核查。")
    return "\n\n".join(sections)


def _method_answer(hits: list[SearchHit]) -> str:
    numbered = [(index, hit.chunk.content.lower()) for index, hit in enumerate(hits[:5], start=1)]

    def citation_for(*signals: str) -> int | None:
        return next((index for index, content in numbered if all(signal in content for signal in signals)), None)

    components: list[str] = []
    persistent = citation_for("persistent-homology", "descriptor")
    if persistent:
        components.append(f"- Persistent Homology 分支：从骨架运动中提取全局拓扑描述符。[{persistent}]")
    film = citation_for("ta-film") or citation_for("feature-wise", "modulation")
    if film:
        components.append(f"- TA-FiLM 拓扑调制：根据 joint、bone 和 motion 等不同模态调整拓扑描述符。[{film}]")
    dynamic = citation_for("dynamic", "graph", "block")
    if dynamic:
        components.append(f"- Dynamic Graph Block：融合 node、edge 与 general-relation 流，并结合 SE、时序卷积和残差连接。[{dynamic}]")
    gates = citation_for("stage-aware", "gate") or citation_for("depth-aware", "gate")
    if gates:
        components.append(f"- Stage-aware Gates：控制拓扑信息在浅层、中层和深层网络中的注入强度。[{gates}]")

    if len(components) >= 2:
        return "模型架构与关键模块\n" + "\n".join(components) + "\n\n以上结构均可在下方对应页码的原文证据中核查。"
    return ""


def _summary_answer(hits: list[SearchHit]) -> str:
    candidates: list[tuple[int, str]] = []
    for citation, hit in enumerate(hits[:4], start=1):
        normalized = re.sub(r"\s+", " ", hit.chunk.content).strip()
        for sentence in re.split(r"(?<=[.!?])\s+|(?<=[.!?])(?=[A-Z])", normalized):
            sentence = sentence.strip()
            if 40 <= len(sentence) <= 650:
                candidates.append((citation, sentence))

    def select(signals: tuple[str, ...]) -> tuple[int, str] | None:
        matches = [item for item in candidates if any(signal in item[1].lower() for signal in signals)]
        if not matches:
            return None
        return max(matches, key=lambda item: sum(signal in item[1].lower() for signal in signals))

    problem = select(("however", "challenge", "fail to", "limitation", "problem"))
    proposal = select(("we propose", "we present", "we develop", "to address this gap", "to address these issues", "introduce"))
    result = select(("achieves", "outperform", "results demonstrate", "experiments show"))
    if not problem or not proposal:
        return ""

    lines = [
        f"研究问题\n- {problem[1][:460]} [{problem[0]}]",
        f"核心方案\n- {proposal[1][:460]} [{proposal[0]}]",
    ]
    method = _method_answer(hits)
    if method:
        method_lines = [line for line in method.splitlines() if line.startswith("-")][:4]
        lines.append("关键贡献\n" + "\n".join(method_lines))
    if result:
        lines.append(f"实验结论\n- {result[1][:460]} [{result[0]}]")
    return "\n\n".join(lines) + "\n\n以上总结仅基于检索到的原文证据。"


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
    if dataset_intent:
        return _dataset_answer(hits)
    method_intent = any(term in question for term in ("方法", "模型", "模块", "架构", "结构", "怎么做"))
    if method_intent:
        structured_method = _method_answer(hits)
        if structured_method:
            return structured_method
    summary_intent = any(term in question for term in ("贡献", "创新", "研究问题", "核心问题", "总结", "讲了什么"))
    if summary_intent:
        structured_summary = _summary_answer(hits)
        if structured_summary:
            return structured_summary
    for number, hit in enumerate(hits[:3], start=1):
        excerpt = _best_excerpt(question, hit)
        if excerpt and excerpt not in seen:
            seen.add(excerpt)
            sentences.append(f"- {excerpt} [{number}]")

    return "证据摘要\n" + "\n".join(sentences) + "\n\n当前为本地证据抽取模式；关键结论请结合下方原文引用核查。"


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
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"], "remote-llm"
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        fallback = _local_answer(question, hits)
        return f"{fallback}\n\n模型服务暂不可用，本次已自动降级为本地证据抽取。", "local-fallback"
