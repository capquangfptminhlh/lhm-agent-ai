"use strict";
/**
 * One-shot OpenRouter Free API connectivity test for the trusted Electron
 * MAIN process. Never expose the API key to renderer, logs or test result.
 *
 * The owner must save the key via OS safeStorage before triggering this.
 * Only the official OpenRouter HTTPS host and a free-only model are allowed.
 */
const https = require("node:https");

const API_HOST = "openrouter.ai";
const API_PATH = "/api/v1/chat/completions";
const DEFAULT_MODEL = "openrouter/free";
const FREE_MODEL = /^[a-z0-9][a-z0-9_.-]*\/[a-z0-9][a-z0-9_.-]*:free$/i;
const MAX_RESPONSE_BYTES = 96 * 1024;
const TIMEOUT_MS = 30000;

function isFreeModel(model) {
  return model === DEFAULT_MODEL || (
    typeof model === "string" && model.length <= 128 && FREE_MODEL.test(model)
  );
}

function safeInteger(n) {
  return Number.isSafeInteger(n) && n >= 0 ? n : null;
}

function describeFailure(status) {
  if(status === 401 || status === 403)
    return "API key không hợp lệ, bị vô hiệu hoặc không có quyền gọi model.";
  if(status === 402)
    return "Tài khoản OpenRouter đang bị từ chối vì trạng thái thanh toán hoặc số dư.";
  if(status === 429)
    return "OpenRouter Free đang giới hạn lượt gọi hoặc tốc độ. Không thử model trả phí.";
  if(status >= 500)
    return "Máy chủ/model miễn phí OpenRouter tạm thời không khả dụng.";
  return "OpenRouter trả lỗi HTTP "+status+". Kiểm tra API key và model Free.";
}

function parseTestResponse(status, value, requestedModel) {
  if(status !== 200) throw new Error(describeFailure(status));
  if(!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("OpenRouter trả dữ liệu không hợp lệ.");
  const model = value.model;
  if(!isFreeModel(model))
    throw new Error("CẢNH BÁO: model thực tế không được xác nhận là Free. Hãy kiểm tra lịch sử chi phí trên OpenRouter.");
  const choices = value.choices;
  const message = Array.isArray(choices) && choices[0] && choices[0].message;
  let answer = message && message.content;
  if(Array.isArray(answer)) answer = answer.filter(x=>x && x.type==="text" && typeof x.text==="string").map(x=>x.text).join(" ");
  if(typeof answer !== "string" || !answer.trim())
    throw new Error("Đã kết nối OpenRouter, nhưng model trả phản hồi rỗng/không hợp lệ.");
  const usage = value.usage && typeof value.usage === "object" ? value.usage : {};
  const reportedCost = usage.cost !== undefined ? usage.cost : value.cost;
  const costNumber = reportedCost === undefined || reportedCost === null ? null : Number(reportedCost);
  if(costNumber !== null && (!Number.isFinite(costNumber) || costNumber < 0))
    throw new Error("OpenRouter trả số liệu phí không hợp lệ.");
  if(costNumber !== null && costNumber > 0)
    throw new Error("CẢNH BÁO: OpenRouter báo có phí. Yêu cầu đã gửi; kiểm tra hóa đơn và không gọi tiếp.");
  return Object.freeze({
    ok:true,
    requested_model:requestedModel,
    actual_model:model,
    response_preview:answer.trim().slice(0,240),
    prompt_tokens:safeInteger(usage.prompt_tokens),
    completion_tokens:safeInteger(usage.completion_tokens),
    reported_cost_usd:costNumber,
    cost_verified:costNumber === 0,
    note:costNumber === null ?
      "Model miễn phí; API không trả chi phí thực tế. Kiểm tra Activity để xác nhận." :
      "OpenRouter báo chi phí 0 USD cho yêu cầu kiểm thử. Kiểm tra Activity để đối chiếu."
  });
}

function requestFreeCompletion(apiKey, {model=DEFAULT_MODEL, request=https.request, timeoutMs=TIMEOUT_MS}={}) {
  if(typeof apiKey !== "string" || !apiKey.trim() || apiKey.length > 600)
    return Promise.reject(new Error("Chưa có OpenRouter API key đã lưu. Anh hãy lưu khóa trước."));
  if(!isFreeModel(model))
    return Promise.reject(new Error("Chỉ chấp nhận openrouter/free hoặc tên model :free chính xác."));
  const payload=JSON.stringify({
    model,
    messages:[{"role":"user","content":"Reply with exactly the word: LHM_OK"}],
    max_tokens:48,
    stream:false,
    usage:{include:true}
  });
  return new Promise((resolve,reject)=>{
    let settled=false;
    const success=value=>{if(!settled){settled=true;resolve(value);}};
    const failure=error=>{if(!settled){settled=true;reject(error);}};
    let req;
    try {
      req=request({
        protocol:"https:",hostname:API_HOST,port:443,path:API_PATH,
        method:"POST",timeout:timeoutMs,
        headers:{
          "Authorization":"Bearer "+apiKey.trim(),
          "Content-Type":"application/json",
          "Accept":"application/json",
          "Content-Length":Buffer.byteLength(payload),
          "X-Title":"LHM Robot Desktop Free Model Test"
        },
        agent:false
      },response=>{
        const chunks=[];
        let size=0;
        response.on("data",chunk=>{
          size+=chunk.length;
          if(size > MAX_RESPONSE_BYTES){
            failure(new Error("Phản hồi OpenRouter vượt giới hạn kích thước."));
            req.destroy();
            return;
          }
          chunks.push(chunk);
        });
        response.on("error",()=>failure(new Error("Lỗi đọc phản hồi OpenRouter.")));
        response.on("end",()=>{
          if(settled)return;
          let body;
          try{body=JSON.parse(Buffer.concat(chunks).toString("utf8"));}
          catch{return failure(new Error("OpenRouter không trả JSON hợp lệ."));}
          try{success(parseTestResponse(response.statusCode,body,model));}
          catch(error){failure(error);}
        });
      });
      req.on("timeout",()=>{
        failure(new Error("OpenRouter Free phản hồi quá chậm (30 giây)."));
        req.destroy();
      });
      req.on("error",()=>failure(new Error("Không kết nối HTTPS tới openrouter.ai. Kiểm tra Internet/DNS/TLS.")));
      req.end(payload);
    }catch{
      failure(new Error("Không tạo được yêu cầu HTTPS tới OpenRouter."));
    }
  });
}

module.exports = {
  DEFAULT_MODEL,
  isFreeModel,
  parseTestResponse,
  requestFreeCompletion,
};
