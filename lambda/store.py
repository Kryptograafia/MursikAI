"""DynamoDB access. One table, one row per task, event or fact.

Keys:

    pk = USER#<user_id>
    sk = DAY#2026-09-29#TASK#<id>     a task on that date
         DAY#2026-09-29#EVENT#<id>    something that happened on that date
         FACT#<id>                    a lasting fact, no date

Everything for one day shares the DAY#<date># prefix, so reading a day is a
single query. Each row has its own id, so completing, modifying or deleting one
entry never rewrites the others.

Validation happens in tools.py. This module trusts what it is given and only
talks to the table.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

TASK = "TASK"
EVENT = "EVENT"

_table = None


class NotFound(Exception):
    """No row with that id. Reported to the model, never raised at the user."""


def read_day(user_id: str, date: str) -> dict[str, list[dict[str, Any]]]:
    """Tasks and events stored for one date, oldest first."""
    rows = _query(user_id, f"DAY#{date}#")
    tasks = [_task_view(row) for row in rows if _kind_of(row) == TASK]
    events = [_event_view(row) for row in rows if _kind_of(row) == EVENT]
    tasks.sort(key=lambda view: view["_at"])
    events.sort(key=lambda view: view["_at"])
    return {"tasks": [_public(v) for v in tasks], "events": [_public(v) for v in events]}


def list_facts(user_id: str) -> list[dict[str, Any]]:
    """Every lasting fact, oldest first."""
    views = [_fact_view(row) for row in _query(user_id, "FACT#")]
    views.sort(key=lambda view: view["_at"])
    return [_public(v) for v in views]


def add_task(user_id: str, date: str, text: str, now: datetime) -> dict[str, Any]:
    entry_id = _put(
        user_id,
        lambda new_id: {
            "sk": _day_sk(date, TASK, new_id),
            "text": text,
            "done": False,
            "at": now.isoformat(timespec="seconds"),
        },
    )
    return {"id": entry_id, "date": date, "text": text, "done": False}


def add_event(user_id: str, date: str, text: str, now: datetime) -> dict[str, Any]:
    stamp = now.isoformat(timespec="seconds")
    entry_id = _put(
        user_id,
        lambda new_id: {
            "sk": _day_sk(date, EVENT, new_id),
            "text": text,
            "at": stamp,
        },
    )
    return {"id": entry_id, "date": date, "text": text, "time": _clock_time(stamp)}


def add_fact(user_id: str, text: str, now: datetime) -> dict[str, Any]:
    entry_id = _put(
        user_id,
        lambda new_id: {
            "sk": f"FACT#{new_id}",
            "text": text,
            "at": now.isoformat(timespec="seconds"),
        },
    )
    return {"id": entry_id, "text": text}


def set_task_done(user_id: str, date: str, entry_id: str, done: bool) -> None:
    _update(user_id, _day_sk(date, TASK, entry_id), {"done": done})


def modify_task(user_id: str, date: str, entry_id: str, text: str) -> None:
    _update(user_id, _day_sk(date, TASK, entry_id), {"text": text})


def modify_event(user_id: str, date: str, entry_id: str, text: str) -> None:
    _update(user_id, _day_sk(date, EVENT, entry_id), {"text": text})


def modify_fact(user_id: str, entry_id: str, text: str) -> None:
    _update(user_id, f"FACT#{entry_id}", {"text": text})


def delete_task(user_id: str, date: str, entry_id: str) -> None:
    _delete(user_id, _day_sk(date, TASK, entry_id))


def delete_event(user_id: str, date: str, entry_id: str) -> None:
    _delete(user_id, _day_sk(date, EVENT, entry_id))


def delete_fact(user_id: str, entry_id: str) -> None:
    _delete(user_id, f"FACT#{entry_id}")


def _table_handle() -> Any:
    # Created on first use and reused by later invocations of the same container.
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def _pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _day_sk(date: str, kind: str, entry_id: str) -> str:
    return f"DAY#{date}#{kind}#{entry_id}"


def _query(user_id: str, prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    params: dict[str, Any] = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
        "ExpressionAttributeValues": {":pk": _pk(user_id), ":prefix": prefix},
    }
    while True:
        page = _table_handle().query(**params)
        rows.extend(page.get("Items", []))
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows
        params["ExclusiveStartKey"] = cursor


def _put(user_id: str, build_row: Any) -> str:
    """Write a new row under a fresh id, retrying once if the id is taken."""
    for _ in range(2):
        entry_id = uuid.uuid4().hex[:10]
        row = {"pk": _pk(user_id), **build_row(entry_id)}
        try:
            _table_handle().put_item(
                Item=row,
                ConditionExpression="attribute_not_exists(sk)",
            )
            return entry_id
        except Exception as exc:
            if not _is_condition_failure(exc):
                raise
    raise RuntimeError("could not allocate an id")


def _update(user_id: str, sk: str, fields: dict[str, Any]) -> None:
    names = {f"#f{i}": key for i, key in enumerate(fields)}
    values = {f":v{i}": value for i, value in enumerate(fields.values())}
    assignments = ", ".join(f"{name} = {value}" for name, value in zip(names, values))
    try:
        _table_handle().update_item(
            Key={"pk": _pk(user_id), "sk": sk},
            UpdateExpression=f"SET {assignments}",
            ConditionExpression="attribute_exists(sk)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except Exception as exc:
        if _is_condition_failure(exc):
            raise NotFound from exc
        raise


def _delete(user_id: str, sk: str) -> None:
    try:
        _table_handle().delete_item(
            Key={"pk": _pk(user_id), "sk": sk},
            ConditionExpression="attribute_exists(sk)",
        )
    except Exception as exc:
        if _is_condition_failure(exc):
            raise NotFound from exc
        raise


def _is_condition_failure(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _kind_of(row: dict[str, Any]) -> str:
    parts = str(row.get("sk", "")).split("#")
    return parts[2] if len(parts) > 3 else ""


def _id_of(row: dict[str, Any]) -> str:
    return str(row.get("sk", "")).rsplit("#", 1)[-1]


def _task_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _id_of(row),
        "text": str(row.get("text", "")),
        "done": bool(row.get("done", False)),
        "_at": _instant(str(row.get("at", ""))),
    }


def _event_view(row: dict[str, Any]) -> dict[str, Any]:
    stamp = str(row.get("at", ""))
    return {
        "id": _id_of(row),
        "text": str(row.get("text", "")),
        "time": _clock_time(stamp),
        "_at": _instant(stamp),
    }


def _fact_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _id_of(row),
        "text": str(row.get("text", "")),
        "_at": _instant(str(row.get("at", ""))),
    }


def _public(view: dict[str, Any]) -> dict[str, Any]:
    """Drop the sort key. Only fields the model should see are returned."""
    return {key: value for key, value in view.items() if not key.startswith("_")}


def _clock_time(stamp: str) -> str:
    """HH:MM for the prompt, so "hommikul" and "õhtul" mean something."""
    return stamp[11:16] if len(stamp) >= 16 else ""


def _instant(stamp: str) -> datetime:
    """Sort by real instant, which keeps order correct on clock-change days."""
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
