# OpenRouter Free Catalog Selector

Uses **one OpenRouter API key**, not separate provider credentials.

When `LHM_LLM_PROVIDER=openrouter`, `OPENROUTER_MODEL=openrouter/free` and
`LHM_OPENROUTER_FREE_AUTO_SELECT=1`, the server fetches
`https://openrouter.ai/api/v1/models` immediately before selecting a model.
The model must have an exact `:free` suffix and all advertised monetary
pricing fields (including input, output, images and request charges, if present)
must explicitly equal zero. Missing catalog, unavailable network, missing
pricing or positive charges => **stop before generation**, not paid fallback.

Available verified models are ranked per agent role using preference hints,
but these hints do **not** prove the model offers tool-use or vision. A
model listed in a screenshot may be renamed, retired, or excluded when live
pricing cannot be verified.

This feature is opt-in, to avoid changing existing tested Electron paths
without a live account and QA. Set `LHM_OPENROUTER_FREE_AUTO_SELECT=1` when
deploying. The existing Free router path remains when unset.

**Limitations:** Catalog pricing is a point-in-time preflight, not an atomic
billing guarantee. Protect the OpenRouter key with a $0 or minimum spending
limit and disable paid fallback on account settings, then verify provider
activity. This change covers the Python OpenRouter text adapter; Electron,
Vision and other provider paths require separate production enforcement.
