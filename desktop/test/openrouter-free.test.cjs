"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {EventEmitter}=require("node:events");
const {
  DEFAULT_MODEL,isFreeModel,parseTestResponse,requestFreeCompletion
}=require("../openrouter-free-test.cjs");

const KEY="test-key-never-send-or-log-me";
const FREE_RESPONSE={
  model:"qwen/qwen3-next-80b-a3b-instruct:free",
  choices:[{message:{role:"assistant",content:"LHM_OK"}}],
  usage:{prompt_tokens:11,completion_tokens:3,cost:0}
};

function fakeTransport(code=200, response=FREE_RESPONSE) {
  const calls=[];
  function request(options,callback){
    const req=new EventEmitter();
    req.destroy=()=>{};
    req.end=payload=>{
      calls.push({options,payload:JSON.parse(payload)});
      process.nextTick(()=>{
        const res=new EventEmitter();
        res.statusCode=code;
        callback(res);
        res.emit("data",Buffer.from(JSON.stringify(response)));
        res.emit("end");
      });
    };
    return req;
  }
  return {request,calls};
}

test("Free only: accepts router or exact :free slug, rejects paid/invalid models",async()=>{
  assert.equal(DEFAULT_MODEL,"openrouter/free");
  for(const m of ["openrouter/free","qwen/qwen3-4b:free","google/gemma-3-27b-it:free"])
    assert.equal(isFreeModel(m),true,m);
  for(const m of ["openrouter/auto","openrouter/paid","anthropic/claude-sonnet","foo:free",
                  "paid/foo","",null,"openrouter/free#hack"])
    assert.equal(isFreeModel(m),false,String(m));
  const fake=fakeTransport();
  await assert.rejects(requestFreeCompletion(KEY,{model:"anthropic/claude-sonnet",request:fake.request}),
                       /Chỉ chấp nhận/);
  assert.equal(fake.calls.length,0,"Paid model must never create a network request");
});

test("Trusted main process makes single HTTPS POST to the Free Router, no paid fallback",async()=>{
  const fake=fakeTransport();
  const result=await requestFreeCompletion(KEY,{request:fake.request});
  assert.equal(fake.calls.length,1);
  const {options,payload}=fake.calls[0];
  assert.equal(options.protocol,"https:");
  assert.equal(options.hostname,"openrouter.ai");
  assert.equal(options.port,443);
  assert.equal(options.path,"/api/v1/chat/completions");
  assert.equal(options.method,"POST");
  assert.equal(options.headers.Authorization,"Bearer "+KEY);
  assert.equal(payload.model,"openrouter/free");
  assert.equal(payload.max_tokens,48);
  assert.equal(payload.stream,false);
  assert.equal(payload.usage.include,true);
  assert.equal(payload.models,undefined,"Paid fallback list must be absent");
  assert.equal(payload.provider,undefined,"Router must select a free provider");
  assert.equal(result.ok,true);
  assert.equal(result.actual_model,FREE_RESPONSE.model);
  assert.equal(result.response_preview,"LHM_OK");
  assert.equal(result.prompt_tokens,11);
  assert.equal(result.completion_tokens,3);
  assert.equal(result.reported_cost_usd,0);
  assert.equal(result.cost_verified,true);
  assert.equal(JSON.stringify(result).includes(KEY),false,"UI result must never include key");
});

test("Invalid key and rate-limit are distinct errors; never attempt paid fallback",async()=>{
  for(const [status,text] of [[401,/không hợp lệ/],[403,/không hợp lệ/],[429,/giới hạn/],[402,/thanh toán|số dư/],[500,/khả dụng/]]){
    const fake=fakeTransport(status,{error:"provider message must not be leaked"});
    await assert.rejects(requestFreeCompletion(KEY,{request:fake.request}),text);
    assert.equal(fake.calls.length,1);
  }
});

test("Unexpected paid model or reported positive cost fails closed",()=>{
  assert.throws(()=>parseTestResponse(200,{...FREE_RESPONSE,model:"anthropic/claude-3"},DEFAULT_MODEL),/CẢNH BÁO/);
  assert.throws(()=>parseTestResponse(200,{...FREE_RESPONSE,usage:{cost:0.00012}},DEFAULT_MODEL),/CẢNH BÁO/);
  assert.throws(()=>parseTestResponse(200,{...FREE_RESPONSE,cost:0.1,usage:{}},DEFAULT_MODEL),/CẢNH BÁO/);
  assert.throws(()=>parseTestResponse(200,{...FREE_RESPONSE,usage:{cost:-1}},DEFAULT_MODEL),/không hợp lệ/);
});

test("If usage cost is missing, report unverified instead of claiming zero",()=>{
  const {usage, ...response}=FREE_RESPONSE;
  const result=parseTestResponse(200,{...response,usage:{prompt_tokens:10,completion_tokens:3}},DEFAULT_MODEL);
  assert.equal(result.reported_cost_usd,null);
  assert.equal(result.cost_verified,false);
  assert.match(result.note,/Activity/);
});

test("Blank key or overlong key fails before network request",async()=>{
  const fake=fakeTransport();
  await assert.rejects(requestFreeCompletion("",{request:fake.request}),/Chưa có/);
  await assert.rejects(requestFreeCompletion("x".repeat(601),{request:fake.request}),/Chưa có/);
  assert.equal(fake.calls.length,0);
});

test("Electron Settings-only IPC never passes decrypted API key back to webpage",()=>{
  const root=path.resolve(__dirname,"..");
  const main=fs.readFileSync(path.join(root,"main.cjs"),"utf8");
  const preload=fs.readFileSync(path.join(root,"settings-preload.cjs"),"utf8");
  const page=fs.readFileSync(path.join(root,"settings.html"),"utf8");
  const js=fs.readFileSync(path.join(root,"settings.js"),"utf8");
  assert.match(main,/ipcMain\.handle\("settings:test-openrouter"/);
  assert.match(main,/event\.sender\.id!==settingsWindow\.webContents\.id/);
  assert.match(main,/requestFreeCompletion\(key\)/);
  assert.match(preload,/testOpenRouter:\(\)=>ipcRenderer\.invoke\("settings:test-openrouter"\)/);
  assert.match(page,/id="openTest"/);
  assert.match(page,/id="openTestResult"/);
  assert.match(js,/lhmSettings\.testOpenRouter\(\)/);
  assert.doesNotMatch(preload,/OPENROUTER_API_KEY|safeStorage/);
});
