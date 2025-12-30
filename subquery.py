"""
title: Subquery
author: René Vögeli
author_url: https://github.com/LordOfTheRats
git_url: https://github.com/LordOfTheRats/open-webui-zammad-tool
description: Native-mode subquery that self-drives tool execution (multi-tool) using Open WebUI internals, preserving files/knowledge and enabled tools (excluding Subquery).
required_open_webui_version: 0.6.0
version: 0.6.8
licence: MIT
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import inspect
import json
import random
import re
import traceback

from fastapi import Request

from open_webui.main import chat_completion
from open_webui.models.users import UserModel
from open_webui.utils.tools import get_tools


def _tail_messages(self, msgs: list, n: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in msgs[-n:]:
        if isinstance(m, dict) and m.get("role") in ("system", "user", "assistant"):
            out.append({"role": m["role"], "content": m.get("content", "")})
    return out


def _filter_kwargs_for_callable(self, func: Any, kw: Dict[str, Any]) -> Dict[str, Any]:
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
        return kw
    allowed = {p.name for p in params}
    return {k: v for k, v in kw.items() if k in allowed}


def _normalize_tool_calls(
    self, tool_calls: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, tc in enumerate(tool_calls or []):
        if not isinstance(tc, dict):
            continue
        tc = dict(tc)
        idx = tc.get("index", i)
        if isinstance(idx, str):
            idx = int(idx) if idx.isdigit() else i
        elif not isinstance(idx, int):
            idx = i
        tc["index"] = idx
        out.append(tc)
    return out


def _extract_text_tool_calls(self, content: str) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    if not content or "<function=" not in content:
        return tool_calls

    func_blocks = re.findall(
        r"<function=([^>\s]+)>(.*?)</function>",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    for i, (name, body) in enumerate(func_blocks):
        args: Dict[str, Any] = {}
        params = re.findall(
            r"<parameter=([^>\s]+)>(.*?)</parameter>",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for k, v in params:
            args[k.strip()] = v.strip()

        tool_calls.append(
            {
                "index": i,  # critical: keep this an int
                "id": f"call_text_{i}",
                "type": "function",
                "function": {
                    "name": name.strip(),
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )

    return tool_calls


def _make_function_name_readable(self, func_name: str) -> str:
    """
    Convert snake_case function names to a more readable format.
    Examples:
      get_ticket -> Get Ticket
      search_issues -> Search Issues
      create_comment -> Create Comment
    """
    # Replace underscores with spaces and title case
    return func_name.replace("_", " ").title()


class Tools:
    def __init__(self):
        self.max_rounds = 8

    async def subquery(
        self,
        prompt: str,
        include_recent_messages: int = 0,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
        __messages__: list | None = None,
        __files__: list | None = None,
        __model__: dict | None = None,
        __request__: Request | None = None,
        __event_emitter__: Any | None = None,
    ) -> str:
        """
        Perform a subquery-style request using the same model that invoked this tool.

        Args:
          prompt: The subquery prompt (e.g., "Summarize the comments on page 1 (10 per page) in issue #123. Focus on the comments. Your summary is part of a larger report.")
          include_recent_messages: If >0, includes that many last messages from the current chat in the subquery.
                                   Default 0 to avoid re-loading big context.
        Returns:
          The assistant text response from the subquery.
        """
        try:
            if __event_emitter__:
                start_messages = [
                    "🤔 Initiating subquery inception... (we need to go deeper)",
                    "🎭 Spawning a mini-me to handle this request...",
                    "🌀 Opening a portal to Subquery Dimension™...",
                    "🔮 Consulting my inner assistant about this...",
                    "🎪 Time for some recursive shenanigans!",
                    "🚀 Launching subquery probe into the unknown...",
                    "💭 Asking myself important questions...",
                    "🎯 Starting subquery session...",
                    "🔄 Entering subquery mode...",
                ]
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": random.choice(start_messages),
                            "done": False,
                        },
                    }
                )

            if __request__ is None:
                raise RuntimeError("[Subquery] __request__ was not injected")
            if __user__ is None:
                raise RuntimeError("[Subquery] __user__ was not injected")
            if not (__model__ and isinstance(__model__, dict) and __model__.get("id")):
                raise RuntimeError(
                    "[Subquery] could not determine current model id from __model__"
                )

            user = UserModel(**__user__)
            model_id: str = __model__["id"]

            # Base messages: DO NOT inject a system message (preserve persona).
            messages: List[Dict[str, Any]] = []
            if include_recent_messages and __messages__:
                messages.extend(
                    _tail_messages(self, __messages__, include_recent_messages)
                )
            messages.append({"role": "user", "content": prompt})

            # Params: inherit, then force native tool calling
            params: Dict[str, Any] = {}
            inherited_tool_ids = None
            if isinstance(__metadata__, dict):
                meta_params = __metadata__.get("params") or {}
                if isinstance(meta_params, dict):
                    params.update(meta_params)
                inherited_tool_ids = __metadata__.get("tool_ids")
            params["function_calling"] = "native"

            # Tools: preserve enabled tools but exclude this tool (case-insensitive)
            tool_ids: Optional[List[str]] = None
            if isinstance(inherited_tool_ids, list):
                tool_ids = [
                    t
                    for t in inherited_tool_ids
                    if isinstance(t, str) and t.lower() != "subquery"
                ]

            # Resolve tools (async). get_tools expects extra_params["__user__"] in your stacktrace.
            tools_registry: Dict[str, Any] = {}
            if tool_ids:
                tools_registry = await get_tools(
                    __request__,
                    tool_ids,
                    user,
                    {"__model__": __model__, "__user__": __user__},
                )

            for round_num in range(self.max_rounds):

                data = await chat_completion(
                    __request__,
                    {
                        "model": model_id,
                        "messages": messages,
                        "stream": False,
                        "params": params,
                        **({"files": __files__} if __files__ else {}),
                        **({"tool_ids": tool_ids} if tool_ids else {}),
                    },
                    user,
                )

                msg = data["choices"][0]["message"]

                # 1) Structured tool_calls if present
                tool_calls = _normalize_tool_calls(self, msg.get("tool_calls") or [])

                # 2) Otherwise parse XML/text tool calls
                if not tool_calls:
                    content = msg.get("content") or ""
                    parsed = _normalize_tool_calls(
                        self, _extract_text_tool_calls(self, content)
                    )
                    if parsed:
                        tool_calls = parsed
                        # keep only the natural language part before the first tool tag
                        msg["content"] = content.split("<function=", 1)[0].rstrip()

                # Done when there are no tool calls (structured or parsed)
                if not tool_calls:
                    if __event_emitter__:
                        complete_messages = [
                            "✨ Subquery complete! Back to reality... 🎯",
                            "🎉 Mission accomplished! Returning to base...",
                            "✅ All done! That was easier than expected!",
                            "🏁 Finished! Closing the loop...",
                            "💫 Success! Collapsing the recursion...",
                            "🎊 Nailed it! Coming back up for air...",
                            "✓ Subquery finished!",
                            "🔙 Returning from subquery...",
                        ]
                        await __event_emitter__(
                            {
                                "type": "status",
                                "data": {
                                    "description": random.choice(complete_messages),
                                    "done": True,
                                    "hidden": True,
                                },
                            }
                        )
                    return (msg.get("content") or "").strip()

                # Emit status about tool calls (only for multiple tools)
                if __event_emitter__ and len(tool_calls) >= 2:
                    tool_names = [tc["function"]["name"] for tc in tool_calls]
                    # Convert function names to readable format
                    readable_names = [
                        _make_function_name_readable(self, name) for name in tool_names
                    ]

                    if len(tool_calls) == 2:
                        double_tool_messages = [
                            f"🛠️ Running: '{readable_names[0]}' + '{readable_names[1]}'",
                            f"👯 Executing: '{readable_names[0]}' and '{readable_names[1]}'",
                            f"🤝 Calling: '{readable_names[0]}' & '{readable_names[1]}'",
                            f"⚔️ Running 2 tools: '{readable_names[0]}', '{readable_names[1]}'",
                            f"🎭 Executing: '{readable_names[0]}' + '{readable_names[1]}'",
                        ]
                        description = random.choice(double_tool_messages)
                    else:
                        quoted_names = [f"'{name}'" for name in readable_names]
                        multi_tool_messages = [
                            f"⚙️ Running {len(tool_calls)} tools: {', '.join(quoted_names)}",
                            f"🎉 Executing {len(tool_calls)} tools: {', '.join(quoted_names)}",
                            f"🎪 Calling {len(tool_calls)} tools: {', '.join(quoted_names)}",
                            f"🚀 Running {len(tool_calls)} tools: {', '.join(quoted_names)}",
                            f"🌟 Executing {len(tool_calls)} tools: {', '.join(quoted_names)}",
                        ]
                        description = random.choice(multi_tool_messages)

                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": description,
                                "done": False,
                            },
                        }
                    )

                # Append assistant tool request message (OpenAI native pattern)
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )

                # Execute each tool call and append tool result messages
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args_raw = tc["function"].get("arguments", "") or ""
                    tc_id = tc.get("id", "")

                    entry = tools_registry.get(name)
                    if not entry:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": name,
                                "content": f"Tool '{name}' not found",
                            }
                        )
                        continue

                    func = entry["callable"]
                    args = json.loads(args_raw) if args_raw.strip() else {}

                    call_kwargs: Dict[str, Any] = dict(args)
                    call_kwargs.update(
                        {
                            "__user__": __user__,
                            "user": user,
                            "__metadata__": __metadata__,
                            "__messages__": messages,
                            "__files__": __files__,
                            "__model__": __model__,
                            "__request__": __request__,
                            "__event_emitter__": __event_emitter__,
                        }
                    )

                    filtered_kwargs = _filter_kwargs_for_callable(
                        self, func, call_kwargs
                    )

                    result = func(**filtered_kwargs)
                    if inspect.isawaitable(result):
                        result = await result

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name,
                            "content": (
                                result
                                if isinstance(result, str)
                                else json.dumps(result, ensure_ascii=False)
                            ),
                        }
                    )

            raise RuntimeError(f"[Subquery] exceeded max_rounds={self.max_rounds}")

        except Exception as e:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "chat:message:error",
                        "data": {
                            "content": str(e),
                        },
                    }
                )
            print("[Subquery] UNHANDLED EXCEPTION:")
            traceback.print_exc()
            raise
