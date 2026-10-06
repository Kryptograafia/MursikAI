"""Lambda entry point for the Estonian assistant.

One request is one spoken turn. The flow:

  validate payload -> read today's rows -> build system prompt
  -> ask Claude -> run any tool it asks for -> repeat -> return the reply

Reading today needs no tool call, so the common question ("mis ma täna tegema
pean?") costs a single Bedrock call. Other dates go through list_tasks and
list_events.

Message text is never logged. It is personal data, and CloudWatch is not the
place for it. Logs carry the request id, tool names and error types only.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import boto3

import prompt
import store
import tools
from clock import tallinn_now
import json
import urllib.request

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL_ID = os.environ["MODEL_ID"]
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "4"))

# Spoken replies are short. This is high enough for a couple of tool calls.
MAX_TOKENS = 512
TEMPERATURE = 0.2

MAX_MESSAGES = 12
MAX_MESSAGE_CHARS = 1000
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Leave room to finish the Bedrock call and return before Lambda kills us.
TIME_FLOOR_MS = 6000

# Spoken fallbacks. The user hears these, so they are Estonian, not error codes.
SAY_BROKEN = "Vabandust, praegu ei saa vastata. Proovi hetke pärast uuesti."
SAY_UNCLEAR = "Vabandust, ma ei saanud aru. Ütle palun uuesti."
SAY_UNFINISHED = "Vabandust, ma ei jõudnud seda lõpetada. Proovi palun uuesti."

_bedrock = boto3.client("bedrock-runtime")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", "-")
    try:
        user_id = _read_user_id(event)
        messages = _read_messages(event)
    except ValueError as exc:
        logger.warning("bad request reason=%s id=%s", exc, request_id)
        return {"reply": SAY_UNCLEAR, "error": "bad_request"}

    try:
        return _run_turn(user_id, messages, context, request_id)
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the user
        logger.error("turn failed type=%s id=%s", type(exc).__name__, request_id)
        return {"reply": SAY_BROKEN, "error": "internal"}


def _run_turn(
    user_id: str,
    messages: list[dict[str, Any]],
    context: Any,
    request_id: str,
) -> dict[str, Any]:
    now = tallinn_now()
    today = now.date().isoformat()

    snapshot = store.read_day(user_id, today)
    facts = store.list_facts(user_id)
    system = prompt.build(now, snapshot["tasks"], snapshot["events"], facts)

    for round_number in range(MAX_TOOL_ROUNDS):
        if _time_left(context) < TIME_FLOOR_MS:
            logger.warning("out of time round=%s id=%s", round_number, request_id)
            break

        response = _bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": tools.SPECS, "toolChoice": {"auto": {}}},
            inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
        )

        reply_message = response["output"]["message"]
        messages = messages + [reply_message]
        requests = _tool_requests(reply_message)

        if not requests:
            spoken = prompt.to_spoken(_text_of(reply_message))
            return {"reply": spoken} if spoken else {"reply": SAY_UNCLEAR, "error": "empty"}

        messages = messages + [
            {
                "role": "user",
                "content": [
                    _run_tool(req, user_id, now, request_id, round_number) for req in requests
                ],
            }
        ]

    logger.error("tool rounds exhausted id=%s", request_id)
    return {"reply": SAY_UNFINISHED, "error": "unfinished"}


def _run_tool(
    request: dict[str, Any],
    user_id: str,
    now: Any,
    request_id: str,
    round_number: int,
) -> dict[str, Any]:
    """Run one tool and wrap the outcome as a Bedrock toolResult block."""
    name = str(request.get("name", ""))
    arguments = request.get("input")
    known = name in tools.NAMES

    # Only known names are logged, so a stray value cannot smuggle text into logs.
    logger.info(
        "tool name=%s round=%s id=%s",
        name if known else "unknown",
        round_number,
        request_id,
    )

    if not known or not isinstance(arguments, dict):
        outcome = {"ok": False, "error": "unknown tool"}
    else:
        outcome = tools.run(name, arguments, user_id=user_id, now=now)

    return {
        "toolResult": {
            "toolUseId": request.get("toolUseId", ""),
            "content": [{"json": outcome}],
            "status": "success" if outcome.get("ok") else "error",
        }
    }


def _read_user_id(event: dict[str, Any]) -> str:
    value = event.get("user_id") if isinstance(event, dict) else None
    if not isinstance(value, str) or not USER_ID_PATTERN.fullmatch(value):
        raise ValueError("user_id")
    return value


def _read_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the chat history and convert it to Bedrock's message shape."""
    raw = event.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("messages")

    turns: list[dict[str, str]] = []
    for item in raw[-MAX_MESSAGES:]:
        if not isinstance(item, dict):
            raise ValueError("messages")
        role = item.get("role")
        text = item.get("content")
        if role not in ("user", "assistant") or not isinstance(text, str):
            raise ValueError("messages")
        text = text.strip()
        if not text or len(text) > MAX_MESSAGE_CHARS:
            raise ValueError("messages")
        turns.append({"role": role, "content": text})

    # Bedrock requires alternating roles starting with the user. The newest
    # message must be the user's, since that is what we are answering.
    if turns[0]["role"] != "user":
        turns = turns[1:]
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("messages")
    if any(a["role"] == b["role"] for a, b in zip(turns, turns[1:])):
        raise ValueError("messages")

    return [{"role": t["role"], "content": [{"text": t["content"]}]} for t in turns]


def _tool_requests(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block["toolUse"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("toolUse"), dict)
    ]


def _text_of(message: dict[str, Any]) -> str:
    parts = [
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts)


def _time_left(context: Any) -> int:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() if callable(getter) else TIME_FLOOR_MS + 1
