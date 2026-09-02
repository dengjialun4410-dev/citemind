import html
import re

import httpx

from ..config import Settings
from .text_cleaning import is_display_noise


async def translate_to_chinese(text: str, settings: Settings) -> tuple[str, str]:
    source = text.strip()
    if not source:
        return "", "local"
    if is_display_noise(source):
        return "该内容属于表格或公式，建议直接查看原始 PDF 页面以避免符号失真。", "local"
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", source))
    if chinese_count >= max(4, len(source) // 3):
        return source, "local"
    if not settings.openai_api_key:
        headers = {"User-Agent": "Mozilla/5.0 CiteMind/0.3"}
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            for endpoint in (
                "https://translate.googleapis.com/translate_a/single",
                "https://translate.google.com/translate_a/single",
            ):
                try:
                    response = await client.get(
                        endpoint,
                        params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": source},
                    )
                    response.raise_for_status()
                    segments = response.json()[0]
                    translated = "".join(segment[0] for segment in segments if segment and segment[0]).strip()
                    if translated:
                        return translated, "google-free"
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
                    continue

            # MyMemory provides a limited no-key endpoint and acts as an
            # independent fallback when Google is blocked on the current network.
            try:
                response = await client.get(
                    "https://api.mymemory.translated.net/get",
                    params={"q": source[:500], "langpair": "en|zh-CN"},
                )
                response.raise_for_status()
                translated = html.unescape(response.json()["responseData"]["translatedText"]).strip()
                if translated and translated.lower() != source[:500].lower():
                    return translated, "mymemory-free"
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                pass
        return "当前电脑无法连接 Google 或 MyMemory 免密钥翻译服务，请检查网络后重试。", "unavailable"
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
