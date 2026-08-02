import httpx

from ..config import Settings
from .text_cleaning import is_display_noise


async def translate_to_chinese(text: str, settings: Settings) -> tuple[str, str]:
    if not text.strip():
        return "", "local"
    if is_display_noise(text):
        return "该内容属于表格或公式，建议直接查看原始 PDF 页面以避免符号失真。", "local"
    if not settings.openai_api_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text},
                )
                response.raise_for_status()
            segments = response.json()[0]
            translated = "".join(segment[0] for segment in segments if segment and segment[0]).strip()
            if translated:
                return translated, "google-free"
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            pass
        return "免费翻译服务暂时不可用，请稍后重试。", "unavailable"
    payload = {
        "model": settings.openai_chat_model,
        "messages": [
            {"role": "system", "content": "你是严谨的学术论文翻译。将用户提供的一句话翻译成简洁、准确的简体中文；保留公式、缩写、变量名、引用编号和专有名词，不要添加解释。"},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip(), "remote-llm"
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        return "翻译服务暂时不可用，请稍后重试。", "unavailable"
