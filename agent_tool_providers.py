"""Bounded, approval-gated REAL tool integrations for LHM Agent OS.

These are HTTP adapters, not embedded third-party applications:
 - Crawl4AI 0.9+ self-hosted or Cloud API for public-site research
 - Twenty workspace REST API to create a CRM Person
 - Chatwoot Application API to create a CRM contact
 - ComfyUI local server to enqueue a fixed, vetted text-to-image workflow

All side effects enter Tool Hub's existing approval + idempotency + audit path.
AI does not receive bearer credentials or arbitrary HTTP/JS execution powers.
"""
from __future__ import annotations

import ipaddress
import os
import re
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

ACTIONS = {
    "crawl4ai.crawl": {"tool":"crawl4ai","writes_external":False,"approval_required":True},
    "twenty.create_person": {"tool":"twenty","writes_external":True,"approval_required":True},
    "chatwoot.create_contact": {"tool":"chatwoot","writes_external":True,"approval_required":True},
    "comfyui.generate_image": {"tool":"comfyui","writes_external":True,"approval_required":True},
}
EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")
HEX = re.compile(r"^[\w .()\-]{1,180}$", re.UNICODE)


def _env(key: str) -> str:
    return os.environ.get(key,"").strip()


def configured(tool: str) -> bool:
    if tool=="crawl4ai":
        return bool(_env("LHM_CRAWL4AI_TOKEN")) and (
            _env("LHM_CRAWL4AI_MODE")=="cloud" or bool(_env("LHM_CRAWL4AI_URL")))
    if tool=="twenty":
        return bool(_env("LHM_TWENTY_URL") and _env("LHM_TWENTY_API_KEY"))
    if tool=="chatwoot":
        return bool(_env("LHM_CHATWOOT_URL") and _env("LHM_CHATWOOT_API_TOKEN")
                    and _env("LHM_CHATWOOT_ACCOUNT_ID").isdigit())
    if tool=="comfyui":
        return bool(_env("LHM_COMFYUI_URL") and _env("LHM_COMFYUI_CHECKPOINT"))
    return False


def _base(key: str, default: str = "") -> str:
    base = _env(key) or default
    parsed=urlparse(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("","/"):
        raise ValueError(f"{key}: server origin must not include path/query/user info")
    if not parsed.hostname:
        raise ValueError(f"{key}: missing server host")
    if parsed.scheme!="https" and not (
       parsed.scheme=="http" and parsed.hostname.lower() in ("127.0.0.1","localhost","::1")):
        raise ValueError(f"{key}: HTTPS required except localhost")
    return base.rstrip("/")


def _text(p: dict[str,Any], key: str, max_len: int, *, required: bool=True) -> str:
    value=p.get(key,"")
    if not isinstance(value,str):
        raise HTTPException(422,f"{key} phải là chuỗi")
    value=value.strip()
    if len(value)>max_len or (required and not value):
        raise HTTPException(422,f"{key} phải có nội dung hợp lệ và tối đa {max_len} ký tự")
    return value


def _url_allowed(target: str) -> bool:
    """User-controlled crawl targets MUST match an operator-approved exact host."""
    parsed=urlparse(target)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port and parsed.port!=443:
        return False
    hostname=parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost","localhost.localdomain"} or hostname.endswith((".local",".internal",".localhost")):
        return False
    try:
        ipaddress.ip_address(hostname)
        return False
    except ValueError:
        pass
    allow={x.strip().lower().rstrip(".") for x in _env("LHM_CRAWL_ALLOWED_HOSTS").split(",") if x.strip()}
    return hostname in allow


def validate_payload(action: str, p: dict[str,Any]) -> None:
    if action not in ACTIONS:
        raise HTTPException(422,"Agent tool chưa được phép sử dụng")
    if action=="crawl4ai.crawl":
        url=_text(p,"url",2000)
        try:
            valid=_url_allowed(url)
        except ValueError:
            valid=False
        if not valid:
            raise HTTPException(422,"URL phải là HTTPS trên tên miền nằm trong LHM_CRAWL_ALLOWED_HOSTS; chặn IP/localhost")
    elif action=="twenty.create_person":
        first=_text(p,"first_name",80)
        last=_text(p,"last_name",80)
        email=_text(p,"email",254)
        if not EMAIL.fullmatch(email):
            raise HTTPException(422,"Email không hợp lệ")
        if p.get("data_origin_confirmed") is not True:
            raise HTTPException(422,"Phải xác nhận nguồn gốc/quyền sử dụng dữ liệu liên hệ")
    elif action=="chatwoot.create_contact":
        name=_text(p,"name",160)
        email=_text(p,"email",254)
        if not EMAIL.fullmatch(email):
            raise HTTPException(422,"Email không hợp lệ")
        if p.get("data_origin_confirmed") is not True:
            raise HTTPException(422,"Phải xác nhận nguồn gốc/quyền sử dụng dữ liệu liên hệ")
    elif action=="comfyui.generate_image":
        _text(p,"prompt",1300)
        _text(p,"negative_prompt",700,required=False)
        width,height=p.get("width",512),p.get("height",512)
        if type(width) is not int or type(height) is not int or width not in (512,768,1024) or height not in (512,768,1024):
            raise HTTPException(422,"Ảnh chỉ cho phép kích thước 512/768/1024")
        steps=p.get("steps",20)
        if type(steps) is not int or not 1<=steps<=30:
            raise HTTPException(422,"Số bước GPU phải từ 1 đến 30")
        seed=p.get("seed",42)
        if type(seed) is not int or not 0<=seed<=2147483647:
            raise HTTPException(422,"Seed phải là số nguyên trong phạm vi cho phép")


def _receipt(data: Any, id_field: str, *keys: str) -> dict[str,Any]:
    """Return only provider-generated identifiers; never return contact PII or secrets."""
    if not isinstance(data,dict):
        raise ValueError("Provider response is not an object")
    item=data
    for k in keys:
        item=item.get(k)
        if not isinstance(item,dict):
            raise ValueError("Provider missing receipt container: "+k)
    ident=item.get(id_field)
    if not isinstance(ident,(int,str)) or not str(ident).strip():
        raise ValueError("Provider did not confirm a record id")
    return {"provider_record_id":str(ident),"confirmation":"provider_accepted_unverified"}


async def _post(url: str, body: dict, headers: dict[str,str], timeout: float=35) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout,connect=10),follow_redirects=False,
                                 trust_env=False) as client:
        res=await client.post(url,headers=headers,json=body)
        res.raise_for_status()
        return res.json()


def _comfyui_graph(p: dict[str,Any]) -> dict[str,Any]:
    checkpoint=_env("LHM_COMFYUI_CHECKPOINT")
    if not checkpoint or not HEX.fullmatch(checkpoint) or "/" in checkpoint or "\\" in checkpoint:
        raise ValueError("Chưa cấu hình checkpoint hợp lệ trong LHM_COMFYUI_CHECKPOINT")
    # Fixed standard core nodes ONLY: no dynamic custom node names or arbitrary
    # graph accepted from user/AI. GPU work is explicitly approved before dispatch.
    return {
      "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
      "2":{"class_type":"CLIPTextEncode","inputs":{"text":p["prompt"],"clip":["1",1]}},
      "3":{"class_type":"CLIPTextEncode","inputs":{"text":p.get("negative_prompt",""),"clip":["1",1]}},
      "4":{"class_type":"EmptyLatentImage","inputs":{"width":p.get("width",512),
          "height":p.get("height",512),"batch_size":1}},
      "5":{"class_type":"KSampler","inputs":{"seed":p.get("seed",42),
          "steps":p.get("steps",20),"cfg":7.0,"sampler_name":"euler",
          "scheduler":"normal","denoise":1.0,"model":["1",0],
          "positive":["2",0],"negative":["3",0],"latent_image":["4",0]}},
      "6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
      "7":{"class_type":"SaveImage","inputs":{"filename_prefix":"LHM_Agent_AI",
          "images":["6",0]}}
    }


async def dispatch(action: str, p: dict[str,Any]) -> dict[str,Any]:
    validate_payload(action,p)
    tool=ACTIONS[action]["tool"]
    if not configured(tool):
        raise ValueError(f"Chưa cấu hình kết nối {tool}")
    if action=="crawl4ai.crawl":
        mode=_env("LHM_CRAWL4AI_MODE") or "selfhost"
        if mode not in ("selfhost","cloud"):
            raise ValueError("LHM_CRAWL4AI_MODE chỉ nhận selfhost/cloud")
        headers={"Authorization":"Bearer "+_env("LHM_CRAWL4AI_TOKEN"),
                 "Content-Type":"application/json"}
        if mode=="cloud":
            url="https://api.crawl4ai.com/scrape"
            body={"url":p["url"],"format":"md"}
        else:
            url=_base("LHM_CRAWL4AI_URL")+"/crawl"
            body={"urls":[p["url"]]}
        data=await _post(url,body,headers,60)
        if isinstance(data,dict) and data.get("success") is False:
            raise ValueError("Crawl4AI rejected crawl request")
        result=data
        if mode=="selfhost":
            entries=data.get("results") if isinstance(data,dict) else data
            if not isinstance(entries,list) or not entries or not isinstance(entries[0],dict):
                raise ValueError("Crawl4AI /crawl missing results")
            result=entries[0]
            if result.get("success") is False:
                raise ValueError("Crawl4AI did not crawl target")
        md=result.get("markdown") if isinstance(result,dict) else None
        if isinstance(md,dict):
            md=md.get("raw_markdown") or md.get("fit_markdown")
        if not isinstance(md,str) or not md.strip():
            raise ValueError("Crawl4AI did not return usable markdown")
        return {"provider":"crawl4ai","source_url":p["url"],
                "markdown_excerpt":md[:12000],"excerpt_truncated":len(md)>12000,
                "confirmation":"extracted_content_unverified"}
    if action=="twenty.create_person":
        data=await _post(_base("LHM_TWENTY_URL")+"/rest/people",{
          "name":{"firstName":p["first_name"],"lastName":p["last_name"]},
          "emails":{"primaryEmail":p["email"]}
        },{"Authorization":"Bearer "+_env("LHM_TWENTY_API_KEY"),
           "Content-Type":"application/json"})
        if isinstance(data,dict) and isinstance(data.get("data"),dict):
            data=data["data"]
            if isinstance(data.get("createPerson"),dict):
                data=data["createPerson"]
            elif isinstance(data.get("person"),dict):
                data=data["person"]
        return {"provider":"twenty",**_receipt(data,"id")}
    if action=="chatwoot.create_contact":
        account=int(_env("LHM_CHATWOOT_ACCOUNT_ID"))
        data=await _post(_base("LHM_CHATWOOT_URL")+f"/api/v1/accounts/{account}/contacts",{
          "name":p["name"],"email":p["email"]
        },{"api_access_token":_env("LHM_CHATWOOT_API_TOKEN"),
           "Content-Type":"application/json"})
        contact=data.get("payload") if isinstance(data,dict) and isinstance(data.get("payload"),dict) else data
        return {"provider":"chatwoot",**_receipt(contact,"id")}
    if action=="comfyui.generate_image":
        data=await _post(_base("LHM_COMFYUI_URL")+"/prompt",{
          "prompt":_comfyui_graph(p),"client_id":str(uuid.uuid4())
        },{"Content-Type":"application/json"},60)
        if not isinstance(data,dict) or not isinstance(data.get("prompt_id"),str) or not data["prompt_id"]:
            raise ValueError("ComfyUI did not accept image job / no prompt_id")
        if data.get("node_errors"):
            raise ValueError("ComfyUI reports graph validation failures")
        return {"provider":"comfyui","prompt_id":data["prompt_id"],
                "confirmation":"image_queued_not_rendered",
                "note":"Kiểm tra history trên ComfyUI; chưa có ảnh nếu chỉ mới xếp hàng"}
    raise ValueError("Unsupported action")


async def comfyui_status(prompt_id: str) -> dict[str,Any]:
    if not configured("comfyui"):
        raise HTTPException(409,"ComfyUI chưa cấu hình")
    if not re.fullmatch(r"[0-9a-fA-F-]{32,40}",prompt_id):
        raise HTTPException(422,"prompt_id không hợp lệ")
    base=_base("LHM_COMFYUI_URL")
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,trust_env=False) as client:
        res=await client.get(base+"/history/"+prompt_id)
        res.raise_for_status()
        data=res.json()
    record=data.get(prompt_id) if isinstance(data,dict) else None
    if not isinstance(record,dict):
        return {"prompt_id":prompt_id,"status":"pending","images":[]}
    info=record.get("status") or {}
    outputs=record.get("outputs") or {}
    image_names=[]
    if isinstance(outputs,dict):
        for output in outputs.values():
            if isinstance(output,dict):
                for x in output.get("images",[])[:10]:
                    if isinstance(x,dict) and isinstance(x.get("filename"),str):
                        image_names.append({"filename":x["filename"][:200],
                            "subfolder":str(x.get("subfolder",""))[:100],
                            "type":str(x.get("type","output"))[:20]})
    return {"prompt_id":prompt_id,
            "status":"finished" if info.get("completed") is True else "provider_recorded",
            "images":image_names[:10]}
