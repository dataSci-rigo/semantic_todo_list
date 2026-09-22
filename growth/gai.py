"""Anthropic calls for growth: Haiku for tagging / grading / re-ranking,
Sonnet for summaries. Structured output via forced tool use (works on the
pinned anthropic 0.50.0). Every call has a timeout and one retry and returns
None on failure — a touch never blocks on the API. anthropic is imported
lazily so the panel can import sibling modules without it.

    python -m growth.gai --selftest   # one tag call + one grade call
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import config as stm

from . import settings

_client = None

WIN_TAGS = ["social_courage", "tried_new_thing", "looked_foolish_anyway", "finished_hard_thing", "willpower_win",
            "kept_promise_to_self", "helped_someone", "stayed_calm", "learned", "chose_right_thing",
            "rested_on_purpose", "other"]
DOMAINS = ["work", "family", "health", "creative", "admin", "social", "other"]


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=stm.ANTHROPIC_API_KEY)
    return _client


def call_tool(model: str, system: str, user_text: str, tool_name: str, schema: dict,
              max_tokens: int = 512) -> dict | None:
    try:
        client = _get_client().with_options(timeout=settings.AI_TIMEOUT, max_retries=1)
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user_text}],
            tools=[{"name": tool_name, "description": f"Record the {tool_name} result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == tool_name:
                return dict(block.input)
    except Exception as e:
        print(f"  growth ai {tool_name} failed: {e}")
    return None


TAG_SYSTEM = ("You tag entries in a personal Success Bank. Pick 1-3 tags from the allowed list, a difficulty 1-5 "
              "(how hard this was for an ordinary person), and one domain. Flat, factual; no praise.")
TAG_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "tags": {"type": "array", "items": {"type": "string", "enum": WIN_TAGS}, "minItems": 1, "maxItems": 3},
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "domain": {"type": "string", "enum": DOMAINS},
    },
    "required": ["tags", "difficulty", "domain"],
}


def tag_win(text: str) -> dict | None:
    out = call_tool(settings.MODEL_FAST, TAG_SYSTEM, text, "tag_win", TAG_SCHEMA, 200)
    if not out:
        return None
    tags = [t for t in out.get("tags", []) if t in WIN_TAGS] or ["other"]
    try:
        diff = max(1, min(5, int(out.get("difficulty", 3))))
    except (TypeError, ValueError):
        diff = 3
    domain = out.get("domain") if out.get("domain") in DOMAINS else "other"
    return {"tags": tags, "difficulty": diff, "domain": domain}


GRADE_SYSTEM = ("You grade a short reflective entry from a self-coaching bot. depth 1-5; explanatory_style of any "
                "setback described: optimistic (temporary, specific, external), pessimistic, mixed, or na; mindset: "
                "growth, fixed, or na. follow_up: one factual sentence of at most 20 words the user will see — "
                "no praise, no 'should', no exclamation marks; name a pattern or the next small step.")
GRADE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "depth": {"type": "integer", "minimum": 1, "maximum": 5},
        "explanatory_style": {"type": "string", "enum": ["optimistic", "pessimistic", "mixed", "na"]},
        "mindset": {"type": "string", "enum": ["growth", "fixed", "na"]},
        "follow_up": {"type": "string"},
    },
    "required": ["depth", "explanatory_style", "mindset", "follow_up"],
}


def grade_entry(kind: str, text: str) -> dict | None:
    out = call_tool(settings.MODEL_FAST, GRADE_SYSTEM, f"Entry kind: {kind}\n\n{text}", "grade_entry",
                    GRADE_SCHEMA, 200)
    if not out:
        return None
    fu = str(out.get("follow_up") or "").strip().replace("!", ".")
    if len(fu.split()) > 24:
        fu = " ".join(fu.split()[:24])
    return {"depth": out.get("depth"), "explanatory_style": out.get("explanatory_style"),
            "mindset": out.get("mindset"), "follow_up": fu}


RERANK_SYSTEM = ("Given a natural-language question about someone's past wins and candidate entries, return the ids "
                 "of the entries that answer it, most relevant first. Omit irrelevant ones.")
RERANK_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ids"]}


def rerank_wins(query: str, candidates: list[dict]) -> list[int] | None:
    if not candidates:
        return None
    payload = json.dumps({"question": query, "candidates": candidates}, ensure_ascii=False)
    out = call_tool(settings.MODEL_FAST, RERANK_SYSTEM, payload, "rerank_wins", RERANK_SCHEMA, 200)
    if not out:
        return None
    return [int(i) for i in out.get("ids", []) if isinstance(i, int)]


VIS_SYSTEM = "Tag a short visualization of a person's future self with 2-6 lowercase keywords: roles, life domains, and task categories it touches."
VIS_SCHEMA = {"type": "object", "additionalProperties": False,
              "properties": {"tags": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
              "required": ["tags"]}


def tag_visualization(text: str, roles: list[str]) -> list[str] | None:
    out = call_tool(settings.MODEL_FAST, VIS_SYSTEM, f"Roles: {', '.join(roles)}\n\n{text}", "tag_visualization",
                    VIS_SCHEMA, 120)
    if not out:
        return None
    return [str(t).lower()[:30] for t in out.get("tags", [])][:6]


def selftest() -> int:
    print("model:", settings.MODEL_FAST)
    t = tag_win("Called the county office after avoiding it for a week; got the permit question answered.")
    print("tag_win:", t)
    g = grade_entry("proud", "I stopped the video and did the tax letter. Only 20 minutes but it moved.")
    print("grade_entry:", g)
    return 0 if (t and g) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="make one tag call and one grade call")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(selftest())
    parser.print_help()


if __name__ == "__main__":
    main()
