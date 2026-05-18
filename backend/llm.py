import json
import httpx
from config import load_settings

SEARCH_STRATEGY_PROMPT = """你是一个知识库搜索助手。用户会提出一个问题，你需要分析问题并生成搜索策略。

请返回 JSON 格式（不要包含 markdown 代码块标记）：
{
    "keywords": ["关键词1", "关键词2", ...],
    "file_pattern": "*",
    "need_full_file": false,
    "reasoning": "简短说明搜索策略"
}

规则：
- keywords: 提取 2-5 个最相关的搜索关键词，包括同义词和相关术语
- file_pattern: 文件名匹配模式，默认 "*" 表示搜索所有文件
- need_full_file: 如果问题需要完整上下文（如"总结某文档"），设为 true

用户问题："""

ANSWER_PROMPT = """你是知识库的智能助手。基于以下从知识库中搜索到的内容，回答用户的问题。

要求：
1. 只基于提供的搜索结果回答，不要编造信息
2. 如果搜索结果不足以回答问题，明确告知用户
3. 在回答末尾标注信息来源（文件名和行号）
4. 使用清晰、自然的中文回答

搜索结果：
{search_results}

用户问题：{question}"""


async def chat_completion(messages: list[dict], temperature: float = 0) -> str:
    settings = load_settings()
    base_url = settings.llm_base_url.rstrip("/")

    system_msg = None
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_msg = msg["content"]
        else:
            user_messages.append(msg)

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": settings.llm_model,
        "messages": user_messages,
        "max_tokens": 4096,
        "temperature": temperature,
    }
    if system_msg:
        payload["system"] = system_msg

    url = f"{base_url}/v1/messages"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()


async def generate_search_strategy(question: str) -> dict:
    content = await chat_completion([
        {"role": "system", "content": SEARCH_STRATEGY_PROMPT},
        {"role": "user", "content": question},
    ], temperature=0)
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"keywords": [question], "file_pattern": "*", "need_full_file": False}


async def generate_answer(question: str, search_results: str) -> str:
    prompt = ANSWER_PROMPT.format(search_results=search_results, question=question)
    return await chat_completion([
        {"role": "user", "content": prompt},
    ], temperature=0.3)
