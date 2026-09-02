import re


_MATH_SIGNS = re.compile(r"[∑∫√≈≠≤≥±×÷∞∂∇∈∉∪∩→←↔α-ωΑ-Ω∥‖−]")
_NUMBERS = re.compile(r"\b\d+(?:\.\d+)?%?\b")
_READER_BOILERPLATE = re.compile(
    r"anonymous submission|review purposes only|distribution, citation, or public sharing|"
    r"copyright and publication details|all rights reserved|proceedings of|"
    r"accepted (?:to|by)|preprint\.?\s+under review",
    re.IGNORECASE,
)


def is_reference_block(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    lowered = compact.lower()
    citation_signals = sum(
        lowered.count(signal)
        for signal in (
            "proceedings", "conference on", "transactions on", "journal of",
            "arxiv preprint", "computer vision–", "computer vision-", "et al.",
        )
    )
    author_fragments = len(re.findall(r"(?:^|[;,]\s*)[A-Z][a-z-]+,\s*[A-Z](?:\.|\s)", compact))
    years = len(re.findall(r"\b(?:19|20)\d{2}\b", compact))
    return lowered.startswith("references") or citation_signals >= 3 or author_fragments >= 5 or (years >= 3 and citation_signals >= 1)


def is_display_noise(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < 8:
        return False
    if len(_MATH_SIGNS.findall(compact)) >= 2:
        return True
    number_count = len(_NUMBERS.findall(compact))
    punctuation_count = len(re.findall(r"[^\w\s]", compact))
    return number_count >= 5 or (punctuation_count / max(1, len(compact)) > 0.24 and number_count >= 2)


def clean_display_text(text: str, fallback: str = "【检测到表格或公式内容，已隐藏乱码；请查看原文页。】") -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", normalized) if item.strip()]
    readable = [item for item in sentences if not is_display_noise(item)]
    if readable:
        return " ".join(readable)
    return fallback


def is_reader_noise(text: str) -> bool:
    """Stricter display-only filter for the click-to-translate reader."""
    compact = re.sub(r"\s+", " ", text).strip()
    lowered = compact.lower()
    prose_words = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", compact)
    if not compact or _READER_BOILERPLATE.search(compact):
        return True
    if re.search(r"\b[\w.+-]+@[\w.-]+\.\w+\b", compact):
        return True
    if re.search(r"\b(?:school|department|faculty|institute|laboratory) of\b|\buniversity\b", lowered):
        return True
    if re.search(r"\b(?:figure|fig\.|table)\s*\d+\s*:", lowered) or lowered.startswith("(better view"):
        return True
    if len(re.findall(r"[;,]", compact)) >= 4 and len(re.findall(r"\b[A-Z][a-z-]+,?\s+[A-Z]", compact)) >= 3:
        return True
    if len(re.findall(r"\b[A-Z][a-z-]+\s+[A-Z][a-z-]+\d*\b", compact)) >= 2 and len(prose_words) <= 8:
        return True
    if is_display_noise(compact):
        return True
    numbers = len(_NUMBERS.findall(compact))
    math_signs = len(_MATH_SIGNS.findall(compact))
    operators = len(re.findall(r"[=<>+*/^]|(?<!\w)-(?=\w)", compact))
    isolated_symbols = len(re.findall(r"(?:^|\s)[A-Za-zα-ωΑ-Ω](?:\s|$)", compact))
    if math_signs and (numbers >= 2 or operators >= 2):
        return True
    if operators >= 4 or isolated_symbols >= 5:
        return True
    if numbers >= 3 and len(prose_words) < 8:
        return True
    if re.match(r"^(?:table|figure|fig\.|algorithm)\s*\d+\s*[:.]", lowered) and numbers >= 2:
        return True
    return False


def clean_reader_text(text: str) -> str:
    """Return only readable prose units for the sentence translation UI."""
    source = (
        text.replace("\x00", " ")
        .replace("ﬁ", "fi")
        .replace("ﬂ", "fl")
        .replace("ﬀ", "ff")
        .replace("ﬃ", "ffi")
        .replace("ﬄ", "ffl")
    )
    if is_reference_block(source):
        return ""
    clean_lines: list[str] = []
    for line in source.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" \t|•")
        if line and not is_reader_noise(line):
            clean_lines.append(line)
    source = " ".join(clean_lines)
    source = re.sub(r"\be\.g\.", "e<prd>g<prd>", source, flags=re.IGNORECASE)
    source = re.sub(r"\bi\.e\.", "i<prd>e<prd>", source, flags=re.IGNORECASE)
    source = re.sub(r"\bet al\.", "et al<prd>", source, flags=re.IGNORECASE)
    units = [
        item.replace("<prd>", ".").strip(" \t|•")
        for item in re.split(r"(?<=[.!?。！？])\s+", source)
        if item.strip()
    ]
    readable: list[str] = []
    seen: set[str] = set()
    for unit_index, unit in enumerate(units):
        unit = re.sub(r"\s+", " ", unit).strip()
        if unit.endswith("-"):
            continue
        if unit.count("(") > unit.count(")"):
            continue
        if unit_index == len(units) - 1 and len(unit.split()) >= 6 and not re.search(r"[.!?。！？)]$", unit):
            continue
        if len(unit) < 18 or is_reader_noise(unit):
            continue
        fingerprint = re.sub(r"\W+", "", unit.lower())[:180]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        readable.append(unit)
    return "\n".join(readable)
