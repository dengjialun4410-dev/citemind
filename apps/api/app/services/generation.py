import re

import httpx

from ..config import Settings
from .retrieval import SearchHit, _terms, expand_query
from .text_cleaning import is_display_noise


def _ranked_evidence_sentences(
    hits: list[SearchHit],
    signals: tuple[str, ...],
    limit: int = 3,
) -> list[tuple[int, str]]:
    candidates: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for citation, hit in enumerate(hits[:5], start=1):
        normalized = re.sub(r"\s+", " ", hit.chunk.content).strip()
        for sentence in re.split(r"(?<=[。！？.!?])\s+|\n+|(?<=[.!?])(?=[A-Z])", normalized):
            sentence = sentence.strip()
            lowered = sentence.lower()
            fingerprint = re.sub(r"\W+", "", lowered)[:160]
            signal_count = sum(signal in lowered for signal in signals)
            citation_heavy = len(re.findall(r"\b(?:19|20)\d{2}\b", sentence)) >= 2
            caption_like = bool(re.match(r"^(?:figure\s*\d*|table\s*\d*|\([a-z]\))", lowered))
            if signal_count and 24 <= len(sentence) <= 650 and fingerprint not in seen and not is_display_noise(sentence) and not citation_heavy and not caption_like:
                seen.add(fingerprint)
                candidates.append((signal_count + hit.score * 0.2, citation, sentence))
    candidates.sort(reverse=True, key=lambda item: item[0])
    return [(citation, sentence) for _, citation, sentence in candidates[:limit]]


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
    dataset_lines = _ranked_evidence_sentences(
        hits, ("dataset", "benchmark", "ntu", "kinetics", "imagenet", "cifar", "split"), 2
    )
    metric_lines = _ranked_evidence_sentences(
        hits, ("metric", "accuracy", "top-1", "top-5", "precision", "recall", "f1", "auc"), 2
    )
    result_lines = _ranked_evidence_sentences(
        hits, ("achieves", "obtains", "outperform", "improves", "results show", "results demonstrate"), 2
    )

    def bullets(items: list[tuple[int, str]], fallback: str) -> str:
        return "\n".join(f"- {sentence} [{citation}]" for citation, sentence in items) if items else f"- {fallback}"

    sections = [
        "数据集\n" + bullets(dataset_lines, "当前证据未明确列出数据集名称"),
        "评价指标\n" + bullets(metric_lines, "当前证据未明确列出评价指标"),
    ]
    if result_lines:
        sections.append("论文报告的结果\n" + bullets(result_lines, "当前证据未明确报告结果"))
    sections.append("关键结论请结合下方页码引用核查。")
    return "\n\n".join(sections)


def _method_answer(hits: list[SearchHit]) -> str:
    components = _ranked_evidence_sentences(
        hits,
        ("we propose", "we introduce", "we develop", "our method", "contains three", "consists of", "architecture", "module", "component", "block", "branch", "pipeline"),
        3,
    )
    if not components:
        return ""
    lines = "\n".join(f"- {sentence} [{citation}]" for citation, sentence in components)
    return "方法与架构\n" + lines + "\n\n以上内容均为原文证据抽取，不补充论文未明确陈述的模块。"


def _definition_answer(question: str, hits: list[SearchHit]) -> str:
    query_terms = _terms(expand_query(question))
    required_phrases: tuple[str, ...] = ()
    if "持久同调" in question or "持续同调" in question:
        required_phrases = ("persistent homology", "persistent-homology")
    candidates: list[tuple[float, int, str]] = []
    for citation, hit in enumerate(hits[:5], start=1):
        normalized = re.sub(r"\s+", " ", hit.chunk.content).strip()
        for sentence in re.split(r"(?<=[。！？.!?])\s+|(?<=[.!?])(?=[A-Z])", normalized):
            sentence = sentence.strip()
            if not 28 <= len(sentence) <= 650 or is_display_noise(sentence):
                continue
            lowered = sentence.lower()
            if required_phrases and not any(phrase in lowered for phrase in required_phrases):
                continue
            overlap = len(query_terms & _terms(sentence)) / max(1, len(query_terms))
            definition_signal = sum(
                weight for signal, weight in (
                    ("defined as", 1.4), ("refers to", 1.3), ("summarizes", 1.2),
                    ("describes", 1.0), ("means", 0.9), (" is ", 0.25), (" are ", 0.25),
                ) if signal in lowered
            )
            if overlap and definition_signal:
                candidates.append((overlap + definition_signal * 0.45 + hit.score * 0.1, citation, sentence))
    if not candidates:
        return ""
    _, citation, sentence = max(candidates, key=lambda item: item[0])
    return f"概念说明\n- {sentence} [{citation}]\n\n该说明直接摘自当前论文；如需更通俗的中文解释，可点击对应证据翻译。"


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
        # Supplementary files often omit the abstract/problem statement but
        # contain a compact overview of the complete method.  Present that
        # evidence with an explicit boundary instead of returning an unrelated
        # method fragment as if it answered the full-paper question.
        for citation, hit in enumerate(hits[:5], start=1):
            lowered = hit.chunk.content.lower()
            if "supplementary overview" not in lowered and "method overview" not in lowered:
                continue
            overview_sentences = [
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+|\n+", hit.chunk.content)
                if 35 <= len(sentence.strip()) <= 650
            ]
            positioning = next(
                (sentence for sentence in overview_sentences if " treats " in f" {sentence.lower()} " or "we propose" in sentence.lower()),
                None,
            )
            procedure = [
                sentence
                for sentence in overview_sentences
                if any(signal in sentence.lower() for signal in ("normalizes", "constructs", "computes", "vectorizes", "injects"))
            ][:2]
            if positioning or procedure:
                sections = [
                    "研究问题\n- 当前上传文件是 Supplementary Material；现有证据没有明确给出主论文的问题陈述，不能据此替作者推断。",
                ]
                if positioning:
                    sections.append(f"核心定位\n- {positioning[:460]} [{citation}]")
                if procedure:
                    sections.append(
                        "主要方法\n" + "\n".join(f"- {sentence[:460]} [{citation}]" for sentence in procedure)
                    )
                sections.append("贡献边界\n- 补充材料给出了方法实现与分析细节；若要完整总结创新点和实验结论，请上传主论文正文。")
                return "\n\n".join(sections) + "\n\n以上内容仅基于当前文档中的明确证据。"
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
    if not hits:
        return "当前知识库中没有找到与问题直接相关的证据，因此本次不作推断。请换一种更具体的问法、选择目标论文，或上传包含该信息的文档。"

    if any(term in question for term in ("什么是", "是什么意思", "定义", "解释")):
        definition = _definition_answer(question, hits)
        if definition:
            return definition

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
    summary_intent = any(term in question for term in ("贡献", "创新", "研究问题", "核心问题", "总结", "讲了什么"))
    if summary_intent:
        structured_summary = _summary_answer(hits)
        if structured_summary:
            return structured_summary
    method_intent = any(term in question for term in ("方法", "模型", "模块", "架构", "结构", "怎么做"))
    if method_intent:
        structured_method = _method_answer(hits)
        if structured_method:
            return structured_method
    for number, hit in enumerate(hits[:3], start=1):
        excerpt = _best_excerpt(question, hit)
        if excerpt and excerpt not in seen:
            seen.add(excerpt)
            sentences.append(f"- {excerpt} [{number}]")

    return "证据摘要\n" + "\n".join(sentences) + "\n\n当前为本地证据抽取模式；关键结论请结合下方原文引用核查。"


async def generate_answer(question: str, hits: list[SearchHit], settings: Settings) -> tuple[str, str]:
    if not hits:
        return (
            "这个问题与当前知识库中已上传的文献没有足够关联，因此我不会使用不相关内容拼凑答案。\n\n"
            "你可以：\n"
            "- 改问论文的方法、数据集、实验结果、创新点或局限性；\n"
            "- 选择另一篇目标文档；\n"
            "- 上传包含该主题的文献后再提问。",
            "relevance-rejection",
        )
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
