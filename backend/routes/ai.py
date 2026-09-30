"""AI 文本生成路由。

提供 AI 流式文本生成接口。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from backend.ai.config import load_config
from backend.ai.openai_provider import OpenAIProvider

router = APIRouter()


class GeneratePayload(BaseModel):
    """AI 生成请求体，包含提示词。"""
    prompt: str


@router.post("/api/ai/generate")
async def generate_text(payload: GeneratePayload):
    """使用 AI 流式生成文本，返回 SSE 事件流。"""
    try:
        cfg = load_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail="配置加载失败")
    provider = OpenAIProvider(cfg)

    async def stream_generator():
        async for chunk in provider.generate_stream(payload.prompt):
            yield chunk

    return StreamingResponse(stream_generator(), media_type="text/plain")
