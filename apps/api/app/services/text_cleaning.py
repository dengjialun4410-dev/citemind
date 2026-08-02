import re


_MATH_SIGNS = re.compile(r"[∑∫√≈≠≤≥±×÷∞∂∇∈∉∪∩→←↔σλμθφψΩ]")
_NUMBERS = re.compile(r"\b\d+(?:\.\d+)?%?\b")


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
