"""Laptop client for the Estonian assistant.

Text only for now: read a typed line, invoke the Lambda, print the reply.
The speech-to-text and text-to-speech steps will replace input() and print()
later, so all the AWS work lives in send_turn() and stays untouched.

No assistant logic here. The Lambda owns the prompt, the tools and the data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_REGION = "eu-north-1"

# Recent turns give the model context for follow-ups ("ja lisa ka piim").
# Kept in memory only, so closing the terminal leaves nothing on disk.
MAX_HISTORY = 12

# Sits just above the Lambda's own 30 second timeout, so a slow turn is reported
# by the function rather than cut off here. Retries are off: a retried invoke
# could add the same task twice if the first response was lost on the way back.
LAMBDA_CONFIG = Config(
    read_timeout=35,
    connect_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 1},
)

MAX_INPUT_CHARS = 1000


class AssistantError(Exception):
    """The Lambda could not be reached or did not return a usable reply."""


def main() -> int:
    _force_utf8_output()
    args = _parse_args()
    client = boto3.client("lambda", region_name=args.region, config=LAMBDA_CONFIG)
    history: list[dict[str, str]] = []

    print("Kirjuta eesti keeles. Välju käsuga /lõpeta.")
    while True:
        try:
            text = input("sina: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not text:
            continue
        if text == "/lõpeta":
            return 0
        if len(text) > MAX_INPUT_CHARS:
            print("Liiga pikk lause.", file=sys.stderr)
            continue

        turn = _trim(history + [{"role": "user", "content": text}])
        try:
            reply = send_turn(client, args.function_name, args.user_id, turn)
        except AssistantError as exc:
            print(f"viga: {exc}", file=sys.stderr)
            continue

        print(f"assistent: {reply}")
        history = _trim(turn + [{"role": "assistant", "content": reply}])


def send_turn(
    client: Any,
    function_name: str,
    user_id: str,
    messages: list[dict[str, str]],
) -> str:
    """Invoke the Lambda and return the spoken reply."""
    payload = json.dumps(
        {"user_id": user_id, "messages": messages},
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        response = client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=payload,
        )
    except (BotoCoreError, ClientError) as exc:
        raise AssistantError(_aws_reason(exc)) from exc

    body = response["Payload"].read()
    if response.get("FunctionError"):
        raise AssistantError("funktsioon ebaõnnestus")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AssistantError("vigane vastus") from exc

    reply = parsed.get("reply") if isinstance(parsed, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise AssistantError("tühi vastus")
    return reply.strip()


def _trim(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the newest turns. Bedrock requires the first message to be a user one."""
    kept = messages[-MAX_HISTORY:]
    if kept and kept[0]["role"] != "user":
        kept = kept[1:]
    return kept


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Talk to the Estonian assistant.")
    parser.add_argument(
        "--function-name",
        default=os.environ.get("ASSISTANT_FUNCTION_NAME"),
        help="Lambda function name. Or set ASSISTANT_FUNCTION_NAME.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", DEFAULT_REGION),
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("ASSISTANT_USER_ID", "me"),
        help="Partition key for this user's rows.",
    )
    args = parser.parse_args()
    if not args.function_name:
        parser.error("--function-name or ASSISTANT_FUNCTION_NAME is required")
    return args


def _aws_reason(exc: Exception) -> str:
    """Short reason without the full boto3 traceback."""
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "AWS viga"))
    return type(exc).__name__


def _force_utf8_output() -> None:
    """Windows consoles default to a codepage that mangles õ, ä and ü."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
