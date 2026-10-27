"""OpenRouter free-model gateway for LHM Agent OS.

Only OpenRouter's free endpoint/model variants are accepted by this adapter.
This deliberately does NOT silently fall back to billable models.
API keys must be supplied server-side through environment variables.
"""
from __future__ import annotations

import os
import re
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
    # Optional strict live-catalog selection. If catalog pricing cannot be verified,\n    # fail closed rather than falling back to any unverified or billable model.\n    if model == "openrouter/free" and os.environ.get("LHM_OPENROUTER_FREE_AUTO_SELECT") == "1":\n        from openrouter_free_catalog import select_free_model\n        try:\n            model = await select_free_model(role)\n        except (httpx.HTTPError, ValueError) as exc:\n            raise HTTPException(503, "Không xác minh được model miễn phí trên OpenRouter; dừng nhiệm vụ") from exc\n    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
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
        message = payload["choices"][0]["message"]["content"]
        usage = payload["usage"]
        if not isinstance(message, str) or not message.strip():
            raise ValueError("missing response text")
        if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), int) \
           or not isinstance(usage.get("completion_tokens"), int):
            raise ValueError("missing token usage")
        # Fail closed on any unexpected positive provider-reported charge.
        if float(usage.get("cost", 0) or 0) > 0 or float(payload.get("cost", 0) or 0) > 0:
            raise HTTPException(502, "OpenRouter báo có chi phí; đã chặn kết quả để đối soát hóa đơn")
        return payload
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
