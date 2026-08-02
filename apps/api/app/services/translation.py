import httpx

from ..config import Settings


async def translate_to_chinese(text: str, settings: Settings) -> tuple[str, str]:
    if not text.strip():
        return "", "local"
    if not settings.openai_api_key:
        return "未配置翻译模型。请在 .env 中设置 OPENAI_API_KEY 后重启 API 服务。", "unavailable"
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
