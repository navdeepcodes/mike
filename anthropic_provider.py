"""Claude through the Anthropic API (official SDK), as one of Mike's model providers.

Separate from Claude Code (brain.py, your Claude plan): this uses an Anthropic API key from
console.anthropic.com and is billed to that account. fallback.py keeps its conversation in the common
chat format; this module converts it to the Messages API and back, and keeps Claude's own blocks
(thinking, tool calls) so a multi-step task replays them unchanged.
"""
import json
import logging
import re

import anthropic

log = logging.getLogger("mike.anthropic")
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_SERVER_FALLBACK = {"claude-haiku-5-5"}      # Haiku has no server-side refusal fallback


class ProviderDown(Exception):
    """This key/model can't answer right now: Mike moves on to the next provider."""


def _client(key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=key, max_retries=1, timeout=60.0)


def _safe_id(i: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", i or "") or "toolu_mike"


def tools(openai_tools: list[dict]) -> list[dict]:
    return [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}},
             "eager_input_streaming": True} for t in openai_tools]


def messages(chat: list[dict]) -> list[dict]:
    """Common chat format -> Messages API turns. Tool results that follow one assistant turn share one user turn."""
    out: list[dict] = []
    for m in chat:
        role = m.get("role")
        if role == "user":
            content = m.get("content") or ""
            if isinstance(content, list):            # text + pasted images (OpenAI-style parts) -> Claude blocks
                blocks = []
                for part in content:
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        url = str((part.get("image_url") or {}).get("url", ""))
                        media, _, data = url.partition(";base64,")
                        blocks.append({"type": "image", "source": {"type": "base64", "data": data,
                                                                   "media_type": media.replace("data:", "") or "image/jpeg"}})
                content = blocks
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            raw = next(((c.get("extra_content") or {}).get("anthropic") for c in calls
                        if (c.get("extra_content") or {}).get("anthropic")), None)
            if raw:                                  # Claude's own turn: replay it exactly as it came back
                out.append({"role": "assistant", "content": raw})
                continue
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for c in calls:
                try:
                    inp = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    inp = {}
                blocks.append({"type": "tool_use", "id": _safe_id(c.get("id")), "name": c["function"]["name"],
                               "input": inp if isinstance(inp, dict) else {}})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            text = m.get("content") or ""
            block = {"type": "tool_result", "tool_use_id": _safe_id(m.get("tool_call_id")), "content": text,
                     **({"is_error": True} if text.startswith(("Bad arguments", "Failed", "Unknown tool")) else {})}
            prev = out[-1] if out else None
            if prev and prev["role"] == "user" and isinstance(prev["content"], list) \
                    and all(b.get("type") == "tool_result" for b in prev["content"]):
                prev["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _valid(inp, schema: dict) -> bool:
    return isinstance(inp, dict) and all(k in inp for k in (schema or {}).get("required", []))


async def turn(key: str, model: str, system: str, chat: list[dict], openai_tools: list[dict], on_text,
               max_tokens: int = 8000, effort: str = "low") -> tuple[list[dict], list]:
    """One model turn, streamed. Returns (tool calls in the common format, Claude's raw content blocks)."""
    schemas = {t["function"]["name"]: t["function"].get("parameters") or {} for t in openai_tools}
    params = dict(model=model, max_tokens=max_tokens, system=system, messages=messages(chat),
                  tools=tools(openai_tools), output_config={"effort": effort})
    if model not in NO_SERVER_FALLBACK:
        params.update(betas=[FALLBACK_BETA], fallbacks="default")
    client = _client(key)
    for attempt in range(2):
        streamed = False
        try:
            async with client.beta.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    streamed = True
                    await on_text(text)
                msg = await stream.get_final_message()
            break
        except ValueError as e:                     # a tool input that isn't valid JSON: ask again (once)
            if streamed or attempt:
                raise ProviderDown(f"invalid tool input from Claude ({e})")
            log.info("Claude sent unparseable tool input; asking again")
        except anthropic.AuthenticationError:
            raise ProviderDown("HTTP 401 the Anthropic key was rejected")
        except anthropic.PermissionDeniedError as e:
            raise ProviderDown(f"HTTP 403 {e.message}")
        except anthropic.NotFoundError:
            raise ProviderDown(f"HTTP 404 model {model} isn't available on this key")
        except anthropic.RateLimitError as e:
            raise ProviderDown(f"HTTP 429 rate limited (try again in {e.response.headers.get('retry-after', '60')}s)")
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 or not streamed:
                raise ProviderDown(f"HTTP {e.status_code} {e.message}")
            raise RuntimeError(f"Claude stopped mid-reply ({e.status_code})")
        except anthropic.APIConnectionError as e:
            if streamed:
                raise RuntimeError("connection dropped mid-reply")
            raise ProviderDown(type(e).__name__)
    if msg.stop_reason == "refusal":
        return [], []                                # nothing to run; whatever was said stands
    uses = [b for b in msg.content if b.type == "tool_use"]
    if uses and msg.stop_reason == "max_tokens":
        raise ProviderDown("reply was cut off mid tool call")
    calls = []
    for b in uses:
        ok = _valid(b.input, schemas.get(b.name, {}))
        calls.append({"id": b.id, "name": b.name,
                      "args": json.dumps(b.input if ok else {"INVALID_JSON": json.dumps(b.input)})})
    return calls, msg.to_dict()["content"]


async def ask(key: str, model: str, system: str, user: str, max_tokens: int = 300) -> str:
    """One short question, no tools."""
    params = dict(model=model, max_tokens=max(max_tokens, 1024), system=system,
                  messages=[{"role": "user", "content": user}], output_config={"effort": "low"})
    if model not in NO_SERVER_FALLBACK:
        params.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        msg = await _client(key).beta.messages.create(**params)
    except anthropic.APIError as e:
        raise ProviderDown(str(e)[:200])
    if msg.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in msg.content if b.type == "text").strip()


async def check(key: str, models: list[str]) -> list[str]:
    """Which of these models this key can use (a free metadata call, no tokens spent)."""
    client, ok = _client(key), []
    for m in models:
        try:
            await client.models.retrieve(m)
            ok.append(m)
        except anthropic.AuthenticationError:
            return []
        except anthropic.APIError as e:
            log.info("Anthropic %s unavailable: %s", m, str(e)[:120])
    return ok
