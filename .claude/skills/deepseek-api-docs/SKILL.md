---
name: deepseek-api-docs
description: Authoritative reference for calling or modifying the DeepSeek API (thinking mode, reasoning_content, multi-round chat, tool calls). Use whenever code touches DeepSeek endpoints, the `deepseek` LLM provider, `LLMType.DEEPSEEK`, `api.deepseek.com`, the `deepseek-chat` / `deepseek-reasoner` / `deepseek-v4-pro` models, the `reasoning_content` / `reasoning_effort` / `thinking` parameters, or anything that builds messages for DeepSeek.
---

# DeepSeek API Skill

When the agent reads, writes, or reviews code that calls or wraps the DeepSeek API, it MUST consult the official documentation before making decisions about request shape, parameters, or message history layout.

## Step 0 — Always do this first

1. Use the `WebFetch` tool to load the two canonical pages and read them in full:
   - <https://api-docs.deepseek.com/zh-cn/guides/thinking_mode>
   - <https://api-docs.deepseek.com/zh-cn/guides/multi_round_chat>
2. If `WebFetch` is unavailable or fails (e.g. offline / restricted), fall back to the rules summarized below — but call this out explicitly to the user.
3. Cite the relevant URL in the response when you justify a parameter / message-layout choice.

Do not skip step 0 just because you "remember" the API. DeepSeek frequently revises thinking-mode rules, parameter names, and tool-calling requirements.

## Triggering scenarios (auto-apply)

Apply this skill whenever any of the following appear in the task or in the touched code:

- The string `deepseek`, `DeepSeek`, `DEEPSEEK`, `LLMType.DEEPSEEK`, `api.deepseek.com`, `deepseek-chat`, `deepseek-reasoner`, `deepseek-v4-pro`.
- Parameters: `reasoning_content`, `reasoning_effort`, `thinking={"type": ...}`, `output_config.effort`.
- The MetaGPT files `metagpt/provider/openai_api.py` (DeepSeek is registered there as an OpenAI-compatible provider) and `metagpt/configs/llm_config.py` (where `LLMType.DEEPSEEK` lives).
- Any new provider, tool, or test that issues chat completions against `https://api.deepseek.com`.

## Core rules to enforce

### 1. Thinking mode parameters

| Concern                  | OpenAI-format                                           | Anthropic-format                              |
| ------------------------ | ------------------------------------------------------- | --------------------------------------------- |
| Toggle thinking          | `extra_body={"thinking": {"type": "enabled/disabled"}}` | —                                             |
| Effort (strength)        | `reasoning_effort="high"` or `"max"`                    | `output_config={"effort": "high"/"max"}`      |

- Default thinking toggle is `enabled`.
- Effort: simple requests default to `high`; agentic requests (Claude Code / OpenCode style) auto-promote to `max`. `low` and `medium` map to `high`; `xhigh` maps to `max`.
- When using the OpenAI SDK, `thinking` must be passed via `extra_body=...` — it is **not** a top-level kwarg.

### 2. Forbidden parameters in thinking mode

`temperature`, `top_p`, `presence_penalty`, `frequency_penalty` are **silently ignored** in thinking mode. Do not assume they take effect; remove them or document that they are no-ops.

### 3. `reasoning_content` field

- Returned alongside `content` on the assistant message.
- In streaming, it arrives as `chunk.choices[0].delta.reasoning_content` (separate from `delta.content`). Accumulate it separately. (MetaGPT's `metagpt/provider/openai_api.py` already does this for the streaming path — keep that behavior intact.)

### 4. Multi-round message concatenation

DeepSeek `/chat/completions` is **stateless** — the client must rebuild `messages` each call.

- Round N+1 = Round N's `messages` + the prior assistant message + the new user message.
- It is sufficient (and idiomatic) to push `response.choices[0].message` directly: it already contains `role`, `content`, `reasoning_content`, and `tool_calls`.

#### Whether to keep `reasoning_content` in history

| Prior assistant turn made tool calls? | Must keep `reasoning_content` in subsequent messages? |
| ------------------------------------- | ----------------------------------------------------- |
| **No**                                | No — server ignores it. Either drop or keep; both OK. |
| **Yes**                               | **YES — must echo it back** on every later request, including all tool sub-rounds within the same user turn. Dropping it returns HTTP 400. |

When in doubt (e.g. building a generic wrapper), keep `reasoning_content` — it is always safe to include and required when tools were used.

### 5. Tool calling within thinking mode

- The model may interleave thoughts and tool calls across multiple sub-rounds before producing a final `content`.
- Loop: call → if `tool_calls` present, run them, append `{"role": "tool", "tool_call_id": ..., "content": ...}`, call again. Stop when `tool_calls is None`.
- Across the entire user turn AND all later turns, `reasoning_content` from any tool-using assistant message must be preserved verbatim.

## Reference snippets

### Minimal thinking-mode call (OpenAI SDK)

```python
response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=messages,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
reasoning_content = response.choices[0].message.reasoning_content
content = response.choices[0].message.content
```

### Streaming accumulation

```python
reasoning_content, content = "", ""
for chunk in response:
    delta = chunk.choices[0].delta
    if getattr(delta, "reasoning_content", None):
        reasoning_content += delta.reasoning_content
    elif delta.content:
        content += delta.content
```

### Multi-round (no tools) — drop or keep `reasoning_content`

```python
messages.append(response.choices[0].message)  # safe: server ignores reasoning_content
messages.append({"role": "user", "content": next_user_msg})
```

### Multi-round (with tool calls) — must keep `reasoning_content`

```python
messages.append(response.choices[0].message)        # contains reasoning_content + tool_calls
messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
# ...later turns must still contain the earlier reasoning_content
```

## Checklist before finishing a DeepSeek-related edit

- [ ] Did you `WebFetch` both official URLs (or explicitly note the fallback)?
- [ ] Are forbidden sampling params (`temperature`, etc.) removed under thinking mode?
- [ ] Is `thinking` passed via `extra_body=` (not as a top-level kwarg)?
- [ ] Is `reasoning_content` accumulated separately in streaming code?
- [ ] If tool calling is involved, is `reasoning_content` round-tripped on every subsequent request?
- [ ] Does `messages` get rebuilt fully each call (stateless API)?
- [ ] Cite the relevant DeepSeek doc URL in the user-facing explanation.

## Sources (always re-fetch)

- 思考模式: <https://api-docs.deepseek.com/zh-cn/guides/thinking_mode>
- 多轮对话: <https://api-docs.deepseek.com/zh-cn/guides/multi_round_chat>
