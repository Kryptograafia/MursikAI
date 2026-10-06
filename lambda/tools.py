"""The thirteen actions Claude can ask for.

SPECS is what Bedrock sees: names, descriptions and argument schemas. run() is
what happens when a tool is asked for. Names are ASCII because Bedrock requires
that, even though everything the user hears is Estonian.

Arguments come from a language model reading speech-recognition output, so
nothing is trusted. Every value is checked here before store.py sees it. A bad
argument or a missing row is returned as {"ok": false, "error": ...} for the
model to explain, never raised as an exception.
"""

from __future__ import annotations

import re
from datetime import date as date_type
from datetime import datetime
from typing import Any

import store

MAX_TEXT_CHARS = 500
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")

_DATE_ARG = {
    "type": "string",
    "description": "Kuupäev kujul YYYY-MM-DD. Puudumisel tänane.",
}
_TEXT_ARG = {"type": "string", "description": "Kirje tekst kasutaja sõnadega."}


class BadArgument(Exception):
    """An argument the model sent cannot be used."""


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            },
        }
    }


SPECS: list[dict[str, Any]] = [
    _spec(
        "add_task",
        "Lisa üks ülesanne. Vaikimisi tänasele päevale.",
        {"text": _TEXT_ARG, "date": _DATE_ARG},
        ["text"],
    ),
    _spec(
        "complete_task",
        "Märgi ülesanne tehtuks. Tagasivõtmiseks anna done väärtus false.",
        {
            "task_id": {"type": "string", "description": "Ülesande id."},
            "date": _DATE_ARG,
            "done": {"type": "boolean", "description": "Vaikimisi true."},
        },
        ["task_id"],
    ),
    _spec(
        "modify_task",
        "Muuda olemasoleva ülesande teksti.",
        {"task_id": {"type": "string"}, "text": _TEXT_ARG, "date": _DATE_ARG},
        ["task_id", "text"],
    ),
    _spec(
        "delete_task",
        "Kustuta ülesanne. Kasuta siis, kui kirje oli eksitus.",
        {"task_id": {"type": "string"}, "date": _DATE_ARG},
        ["task_id"],
    ),
    _spec(
        "list_tasks",
        "Loe ühe kuupäeva ülesanded. Tänase kohta ära kasuta, see on juba teada.",
        {"date": _DATE_ARG},
        ["date"],
    ),
    _spec(
        "add_event",
        "Kirjuta üles midagi, mis juhtus. Vaikimisi tänasele päevale.",
        {"text": _TEXT_ARG, "date": _DATE_ARG},
        ["text"],
    ),
    _spec(
        "list_events",
        "Loe ühe kuupäeva sündmused. Tänase kohta ära kasuta, see on juba teada.",
        {"date": _DATE_ARG},
        ["date"],
    ),
    _spec(
        "modify_event",
        "Muuda olemasoleva sündmuse teksti.",
        {"event_id": {"type": "string"}, "text": _TEXT_ARG, "date": _DATE_ARG},
        ["event_id", "text"],
    ),
    _spec(
        "delete_event",
        "Kustuta sündmus. Kasuta siis, kui kirje oli eksitus.",
        {"event_id": {"type": "string"}, "date": _DATE_ARG},
        ["event_id"],
    ),
    _spec(
        "add_fact",
        "Salvesta püsiv fakt kasutaja kohta, näiteks eelistus.",
        {"text": _TEXT_ARG},
        ["text"],
    ),
    _spec(
        "modify_fact",
        "Muuda olemasoleva fakti teksti.",
        {"fact_id": {"type": "string"}, "text": _TEXT_ARG},
        ["fact_id", "text"],
    ),
    _spec(
        "delete_fact",
        "Kustuta püsiv fakt.",
        {"fact_id": {"type": "string"}},
        ["fact_id"],
    ),
    _spec("list_facts", "Loe kõik püsivad faktid.", {}, []),
]

NAMES = frozenset(spec["toolSpec"]["name"] for spec in SPECS)


def run(name: str, arguments: dict[str, Any], *, user_id: str, now: datetime) -> dict[str, Any]:
    """Run one tool. Failures are returned, not raised."""
    try:
        return _ACTIONS[name](arguments, user_id, now)
    except BadArgument as exc:
        return {"ok": False, "error": str(exc)}
    except store.NotFound:
        return {"ok": False, "error": "no entry with that id"}


def _add_task(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    saved = store.add_task(user_id, day, _text(args), now)
    return {"ok": True, **saved}


def _complete_task(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    entry_id = _entry_id(args, "task_id")
    done = _flag(args.get("done"))
    store.set_task_done(user_id, day, entry_id, done)
    return {"ok": True, "id": entry_id, "date": day, "done": done}


def _modify_task(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    entry_id = _entry_id(args, "task_id")
    text = _text(args)
    store.modify_task(user_id, day, entry_id, text)
    return {"ok": True, "id": entry_id, "date": day, "text": text}


def _delete_task(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    entry_id = _entry_id(args, "task_id")
    store.delete_task(user_id, day, entry_id)
    return {"ok": True, "id": entry_id, "date": day}


def _list_tasks(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _required_date(args)
    return {"ok": True, "date": day, "tasks": store.read_day(user_id, day)["tasks"]}


def _add_event(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    saved = store.add_event(user_id, day, _text(args), now)
    return {"ok": True, **saved}


def _list_events(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _required_date(args)
    return {"ok": True, "date": day, "events": store.read_day(user_id, day)["events"]}


def _modify_event(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    entry_id = _entry_id(args, "event_id")
    text = _text(args)
    store.modify_event(user_id, day, entry_id, text)
    return {"ok": True, "id": entry_id, "date": day, "text": text}


def _delete_event(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    day = _date(args, now)
    entry_id = _entry_id(args, "event_id")
    store.delete_event(user_id, day, entry_id)
    return {"ok": True, "id": entry_id, "date": day}


def _add_fact(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    return {"ok": True, **store.add_fact(user_id, _text(args), now)}


def _modify_fact(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    entry_id = _entry_id(args, "fact_id")
    text = _text(args)
    store.modify_fact(user_id, entry_id, text)
    return {"ok": True, "id": entry_id, "text": text}


def _delete_fact(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    entry_id = _entry_id(args, "fact_id")
    store.delete_fact(user_id, entry_id)
    return {"ok": True, "id": entry_id}


def _list_facts(args: dict[str, Any], user_id: str, now: datetime) -> dict[str, Any]:
    return {"ok": True, "facts": store.list_facts(user_id)}


_ACTIONS = {
    "add_task": _add_task,
    "complete_task": _complete_task,
    "modify_task": _modify_task,
    "delete_task": _delete_task,
    "list_tasks": _list_tasks,
    "add_event": _add_event,
    "list_events": _list_events,
    "modify_event": _modify_event,
    "delete_event": _delete_event,
    "add_fact": _add_fact,
    "modify_fact": _modify_fact,
    "delete_fact": _delete_fact,
    "list_facts": _list_facts,
}


def _text(args: dict[str, Any]) -> str:
    value = args.get("text")
    if not isinstance(value, str):
        raise BadArgument("text is required")
    text = " ".join(value.split())
    if not text:
        raise BadArgument("text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise BadArgument("text is too long")
    return text


def _entry_id(args: dict[str, Any], field: str) -> str:
    value = args.get(field)
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise BadArgument(f"{field} is invalid")
    return value


def _required_date(args: dict[str, Any]) -> str:
    return _parse_date(args.get("date"))


def _date(args: dict[str, Any], now: datetime) -> str:
    """Date argument, defaulting to today in Tallinn."""
    value = args.get("date")
    if value is None or value == "":
        return now.date().isoformat()
    return _parse_date(value)


def _parse_date(value: Any) -> str:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise BadArgument("date must be YYYY-MM-DD")
    try:
        date_type.fromisoformat(value)
    except ValueError as exc:
        raise BadArgument("date is not a real date") from exc
    return value


def _flag(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    raise BadArgument("done must be true or false")
