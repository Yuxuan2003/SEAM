"""Model clients for the SEAM agent loop.

Two providers, one record format:

  openai     any OpenAI-compatible chat-completions endpoint (official APIs,
             vLLM, SGLang, OpenRouter, ...). The endpoint and key come from the
             environment variables named in the model config (defaults
             OPENAI_BASE_URL / OPENAI_API_KEY); keys are never written to disk
             or to run records.
  anthropic  the official Anthropic SDK (ANTHROPIC_API_KEY).

Every call returns a dict and never raises:

  ok, content, reasoning, usage{prompt_tokens, completion_tokens},
  finish_reason, error, returned_model, tool_calls (if any), latency_s

A call that succeeds but returns no content is reported as failed with the
error text "empty content although the call succeeded (...)": the agent loop
uses it to tell a thinking model that ran out of its token budget (a censored,
model-side outcome) from an infrastructure failure.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

REASONING_KEYS = ("reasoning", "reasoning_content")
EMPTY = "empty content although the call succeeded"
#: Anthropic extended-thinking budget used in the paper's "thinking on" rows
THINKING_BUDGET = 10240


def user_content(prompt: str, image_b64s: List[str], mime: str = "image/png") -> List[Dict[str, Any]]:
    """One multimodal user turn in OpenAI format (text, then images)."""
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in image_b64s:
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


def _secrets() -> List[str]:
    return [v for k, v in os.environ.items()
            if v and len(v) >= 12 and any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))]


def redact(s: Any) -> str:
    """Remove any API key present in the environment from an error string."""
    out = str(s if s is not None else "")
    for v in _secrets():
        out = out.replace(v, "<REDACTED>")
    return out


def chat(messages: List[Dict[str, Any]], cfg: Dict[str, Any], *, max_tokens: Optional[int] = None,
         extra_body: Optional[Dict[str, Any]] = "__cfg__", temperature: float = 0.2,
         timeout: float = 600.0) -> Dict[str, Any]:
    """One non-streaming call for the model described by `cfg` (see configs/models.json)."""
    max_tokens = int(max_tokens or cfg["max_tokens"])
    body = cfg.get("extra_body") if extra_body == "__cfg__" else extra_body
    if cfg.get("provider", "openai") == "anthropic":
        return _anthropic(messages, cfg, max_tokens, body, temperature, timeout)
    return _openai(messages, cfg, max_tokens, body, temperature, timeout)


def _openai(messages, cfg, max_tokens, extra_body, temperature, timeout) -> Dict[str, Any]:
    t0 = time.time()
    model = cfg["model"]
    rec: Dict[str, Any] = {"model": model, "ok": False, "content": "", "reasoning": "",
                           "usage": {}, "latency_s": 0.0, "error": "", "returned_model": ""}
    try:
        from openai import OpenAI
        base = os.environ.get(cfg.get("base_url_env", "OPENAI_BASE_URL")) or cfg.get("base_url") or None
        key = os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"), "EMPTY")
        cli = OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=0)
        resp = cli.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
            stream=False, extra_body=extra_body or {})
    except Exception as exc:                                   # noqa: BLE001
        rec["error"] = redact(f"{type(exc).__name__}: {exc}")[:2000]
        rec["latency_s"] = round(time.time() - t0, 3)
        return rec
    try:
        msg = resp.choices[0].message.model_dump()
        rec["content"] = msg.get("content") or ""
        for k in REASONING_KEYS:
            if msg.get(k):
                rec["reasoning"] = msg[k]
                break
        # a native tool call leaves `content` empty; keep it so the parser can
        # read the action from it
        tc = msg.get("tool_calls") or []
        if tc:
            rec["tool_calls"] = [{"name": (c.get("function") or {}).get("name"),
                                  "arguments": (c.get("function") or {}).get("arguments")} for c in tc]
        rec["returned_model"] = getattr(resp, "model", "") or ""
        rec["usage"] = resp.usage.model_dump() if resp.usage else {}
        rec["finish_reason"] = resp.choices[0].finish_reason
    except Exception as exc:                                   # noqa: BLE001
        rec["error"] = redact(exc)
    rec["latency_s"] = round(time.time() - t0, 3)
    if not rec["content"] and not rec["error"]:
        ct = (rec["usage"] or {}).get("completion_tokens")
        rec["error"] = f"{EMPTY} (completion_tokens={ct}, max_tokens={max_tokens})"
    rec["ok"] = bool(rec["content"]) and not rec["error"]
    return rec


def _anthropic_messages(messages: List[Dict[str, Any]]):
    system, out = "", []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
            continue
        c = m["content"]
        if isinstance(c, str):
            blocks = [{"type": "text", "text": c}]
        else:
            blocks = []
            for part in c:
                if part.get("type") == "text":
                    blocks.append({"type": "text", "text": part["text"]})
                elif part.get("type") == "image_url":
                    head, data = part["image_url"]["url"].split(",", 1)
                    mime = head.split(";")[0].split(":", 1)[1] if head.startswith("data:") else "image/png"
                    blocks.append({"type": "image",
                                   "source": {"type": "base64", "media_type": mime, "data": data}})
        out.append({"role": m["role"], "content": blocks})
    return system, out


def _anthropic(messages, cfg, max_tokens, extra_body, temperature, timeout) -> Dict[str, Any]:
    """Earlier assistant turns are sent as plain text without thinking blocks:
    the agent uses no tool calls, so the API does not require them back.

    extra_body keys understood here:
      thinking: true          extended thinking with THINKING_BUDGET tokens
      thinking: "adaptive"    adaptive thinking (models that support it)
      effort: "low"|"high"    output effort (models that support it)
    Temperature is only sent when thinking is off (the API rejects it otherwise).
    """
    t0 = time.time()
    model = cfg["model"]
    rec: Dict[str, Any] = {"model": model, "ok": False, "content": "", "reasoning": "",
                           "usage": {}, "latency_s": 0.0, "error": "", "returned_model": ""}
    eb = extra_body or {}
    try:
        import anthropic                                      # pip install anthropic
        cli = anthropic.Anthropic(api_key=os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY")),
                                  max_retries=0)
        system, msgs = _anthropic_messages(messages)
        kw: Dict[str, Any] = dict(model=model, system=system, messages=msgs,
                                  max_tokens=max_tokens, timeout=timeout)
        think = eb.get("thinking")
        if think == "adaptive":
            kw["thinking"] = {"type": "adaptive"}
        elif think:
            kw["thinking"] = {"type": "enabled", "budget_tokens": min(THINKING_BUDGET, max_tokens - 1024)}
        else:
            kw["temperature"] = temperature
        if eb.get("effort"):
            kw["extra_body"] = {"output_config": {"effort": eb["effort"]}}
        resp = cli.messages.create(**kw)
    except Exception as exc:                                   # noqa: BLE001
        # the message carries the HTTP status (429 / 529 are retried by the
        # agent); it never carries the key
        rec["error"] = redact(f"{type(exc).__name__}: {exc}")[:2000]
        rec["latency_s"] = round(time.time() - t0, 3)
        return rec
    rec["content"] = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    rec["reasoning"] = "\n".join(getattr(b, "thinking", "") or "" for b in resp.content
                                 if getattr(b, "type", "") == "thinking")
    rec["returned_model"] = getattr(resp, "model", "") or ""
    u = getattr(resp, "usage", None)
    rec["usage"] = {"prompt_tokens": getattr(u, "input_tokens", 0) or 0,
                    "completion_tokens": getattr(u, "output_tokens", 0) or 0}
    rec["finish_reason"] = getattr(resp, "stop_reason", "")
    if not rec["content"]:
        rec["error"] = f"{EMPTY} (completion_tokens={rec['usage']['completion_tokens']}, max_tokens={max_tokens})"
    rec["ok"] = bool(rec["content"]) and not rec["error"]
    rec["latency_s"] = round(time.time() - t0, 3)
    return rec


def is_budget_exhausted(r: Dict) -> bool:
    """The call returned no content (see EMPTY)."""
    return EMPTY in str(r.get("error") or "")
