"""OpenRouter free-model gateway for LHM Agent OS.

Only OpenRouter's free endpoint/model variants are accepted by this adapter.
This deliberately does NOT silently fall back to billable models.
API keys must be supplied server-side through environment variables.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import HTTPException

from core import role_prompt
from publication_gate import MAX_HANDOFF_CHARS

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FREE_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*:free$", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE = re.compile(r"(?<!\d)(?:\+84|0)[\s.-]?(?:3|5|7|8|9)(?:[\s.-]?\d){8}(?!\d)")


def is_free_model(model: str) -> bool:
    """Allow generic free router or an explicit provider/model:free slug."""
    return model == "openrouter/free" or bool(FREE_MODEL_RE.fullmatch(model))


def provider_for_model(model: str) -> str:
    return "openrouter" if is_free_model(model) else "deepseek"


def model_configured(model: str) -> bool:
    key = "OPENROUTER_API_KEY" if is_free_model(model) else "DEEPSEEK_API_KEY"
    return bool(os.environ.get(key, "").strip())


def validate_model(model: str) -> None:
    """Never accept a paid OpenRouter model through this free adapter."""
    if not isinstance(model, str) or len(model) > 128:
        raise HTTPException(422, "Model không hợp lệ")
    if os.environ.get("LHM_OPENROUTER_FREE_ONLY") == "1" and not is_free_model(model):
        raise HTTPException(422, "OpenRouter Free Only: từ chối tất cả model trả phí")
    if model.startswith("openrouter/") or model.endswith(":free") or "/" in model:
        if not is_free_model(model):
            raise HTTPException(422, "Chỉ hỗ trợ OpenRouter free; không tự chuyển sang model mất phí")


def free_quota_limits() -> tuple[int, int]:
    """Local safety caps, not OpenRouter's authoritative account-wide quota."""
    def get(name: str, default: int, max_value: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            value = default
        return max(1, min(value, max_value))
    return (get("LHM_OPENROUTER_24H_LIMIT", 40, 1000),
            get("LHM_OPENROUTER_1M_LIMIT", 15, 20))


def local_free_quota(c: Any, epoch: int) -> dict[str, int]:
    daily_limit, minute_limit = free_quota_limits()
    daily = int(c.execute("SELECT COUNT(*) FROM openrouter_attempts WHERE created_at>?",
                          (epoch - 86400,)).fetchone()[0])
    minute = int(c.execute("SELECT COUNT(*) FROM openrouter_attempts WHERE created_at>?",
                           (epoch - 60,)).fetchone()[0])
    return {"used_24h": daily, "limit_24h": daily_limit,
            "used_1m": minute, "limit_1m": minute_limit}


def validate_free_chat_reply(payload: Any, requested: str) -> dict[str, Any]:
    """Fail closed with SAFE error categories; never print raw upstream content.

    Some Free endpoints return reasoning without final text, malformed usage,
    or a non-text response. These are not valid completed Agent replies and
    must never be persisted or silently counted as successful.
    """
    if not isinstance(payload, dict):
        raise HTTPException(502, "OpenRouter: phản hồi không phải JSON object")
    actual = payload.get("model")
    if actual is not None and (not isinstance(actual, str) or
                              not is_free_model(actual) or actual == "openrouter/free"):
        raise HTTPException(502, "OpenRouter: model trả về không xác minh được :free")
    if requested != "openrouter/free" and actual is not None and actual != requested:
        raise HTTPException(502, "OpenRouter: model trả về khác model Free đã chọn")
    choices=payload.get("choices")
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        raise HTTPException(502, "OpenRouter: thiếu choices")
    message=choices[0].get("message")
    if not isinstance(message,dict):
        raise HTTPException(502, "OpenRouter: thiếu message")
    content=message.get("content")
    if isinstance(content,list):
        pieces=[]
        for piece in content:
            if not isinstance(piece,dict) or piece.get("type")!="text" or not isinstance(piece.get("text"),str):
                raise HTTPException(502, "OpenRouter: message không phải nội dung văn bản")
            pieces.append(piece["text"])
        content="".join(pieces)
        message["content"]=content
    if not isinstance(content,str) or not content.strip():
        # A Free model may consume its output budget in reasoning only.
        # Fail closed, never invent a final answer or silently retry a paid route.
        finish=choices[0].get("finish_reason")
        if finish=="length":
            raise HTTPException(502, "OpenRouter: model Free hết giới hạn token trước câu trả lời; cần đổi model văn bản")
        raise HTTPException(502, "OpenRouter: model Free thiếu câu trả lời văn bản; không lưu kết quả giả")
    usage=payload.get("usage")
    if not isinstance(usage,dict):
        raise HTTPException(502, "OpenRouter: thiếu thống kê token")
    for name in ("prompt_tokens","completion_tokens"):
        number=usage.get(name)
        if type(number) is not int or number<0:
            raise HTTPException(502, "OpenRouter: thống kê token không hợp lệ")
    costs=[v for v in (usage.get("cost"),payload.get("cost")) if v is not None]
    if not costs and os.environ.get("LHM_OPENROUTER_FREE_ONLY")=="1":
        raise HTTPException(502, "OpenRouter: thiếu xác nhận chi phí 0 USD")
    for value in costs:
        if value is None or isinstance(value,bool):
            raise HTTPException(502, "OpenRouter: dữ liệu chi phí không hợp lệ")
        try:
            cost=Decimal(str(value))
        except (InvalidOperation,ValueError,TypeError) as exc:
            raise HTTPException(502, "OpenRouter: dữ liệu chi phí không hợp lệ") from exc
        if not cost.is_finite() or cost != 0:
            raise HTTPException(502, "OpenRouter: chi phí khác 0 hoặc không hợp lệ")
    return payload


async def call_free_model(role: str, ws: Any, instruction: str, context: str,
                          model: str) -> dict[str, Any]:
    if not is_free_model(model):
        raise HTTPException(422, "Không phải model OpenRouter free")
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Chưa cấu hình OPENROUTER_API_KEY")
    if len(context) > MAX_HANDOFF_CHARS:
        raise HTTPException(422, "Nội dung bàn giao vượt giới hạn")
    joined = instruction + "\n" + context + "\n" + str(ws.get("business_context", ""))
    # Free providers can have different retention/training rules. Refuse common
    # personal-contact markers unless an operator has explicitly opted in.
    if os.environ.get("LHM_OPENROUTER_ALLOW_CONTACT_DATA", "0") != "1":
        if EMAIL.search(joined) or PHONE.search(joined):
            raise HTTPException(422, "Model free từ chối dữ liệu liên hệ cá nhân; dùng model riêng được duyệt")
    system = role_prompt(role, ws["industry"], ws["business_context"])
    user = instruction[:8000] + (
        "\n\nDỮ LIỆU ĐẦU VÀO (chưa kiểm chứng):\n" + context if context else ""
    )
    # Check the live OpenRouter catalog before selecting any explicit free model.
    if model == "openrouter/free" and (os.environ.get("LHM_OPENROUTER_FREE_AUTO_SELECT") == "1" or os.environ.get("LHM_OPENROUTER_FREE_ONLY") == "1"):
        from openrouter_free_catalog import select_free_model
        try:
            model = await select_free_model(role)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "Không xác minh được model miễn phí; dừng nhiệm vụ") from exc
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Title": "LHM Agent OS"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json={
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": 1100,
                "stream": False
            })
        response.raise_for_status()
        payload = response.json()
        return validate_free_chat_reply(payload, model)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            raise HTTPException(429, "OpenRouter free hết quota hoặc giới hạn tốc độ; không gọi model trả phí") from exc
        raise HTTPException(503 if code in (500, 502, 503, 504) else 502,
                            f"OpenRouter lỗi HTTP {code}; không tự trả phí") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, "OpenRouter mất kết nối hoặc quá thời gian") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, "OpenRouter trả kết quả không hợp lệ; không lưu kết quả giả") from exc
