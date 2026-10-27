"use strict";
/**
 * Opt-in integration smoke. Run only with a disposable, tightly limited
 * OpenRouter key stored in OPENROUTER_TEST_API_KEY (GitHub Secret).
 * No key, headers, response bodies, or provider errors are printed.
 */
const {requestFreeCompletion}=require("../openrouter-free-test.cjs");

(async()=>{
  const apiKey=process.env.OPENROUTER_TEST_API_KEY||"";
  if(!apiKey.trim()) {
    process.stderr.write("LIVE NOT RUN: OPENROUTER_TEST_API_KEY GitHub Secret missing.\n");
    process.exitCode=2;
    return;
  }
  try {
    const response=await requestFreeCompletion(apiKey);
    const obj={
      success:response.ok,
      requested_model:response.requested_model,
      actual_free_model:response.actual_model,
      tokens_in:response.prompt_tokens,
      tokens_out:response.completion_tokens,
      reported_cost_usd:response.reported_cost_usd,
      cost_reported:response.cost_verified,
      note:response.note
    };
    process.stdout.write("OPENROUTER FREE LIVE SMOKE RESULT: "+JSON.stringify(obj)+"\n");
  }catch(error){
    // The helper only emits static diagnostic messages; no token or raw
    // provider error is included, even on auth failure.
    process.stderr.write("OPENROUTER FREE LIVE SMOKE FAILED: "+String(error.message||"Unknown error")+"\n");
    process.exitCode=1;
  }
})();
