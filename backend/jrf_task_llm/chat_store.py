from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .config import LOCAL_DATA_DIR
from .local_storage import read_json, write_json

LOCAL_CHAT_FILE = Path(LOCAL_DATA_DIR) / "chats.json"
_LOCAL_LOCK = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def create_chat(document_ids: list[str], model_ids: list[str], title: str) -> tuple[str, str]:
    chat_id = str(uuid4())
    now = _now()
    item = {
        "chat_id": chat_id,
        "title": title[:120],
        "document_ids": document_ids,
        "model_ids": model_ids,
        "created_at": now,
        "updated_at": now,
        "turn_count": 0,
        "record_type": "chat",
    }
    with _LOCAL_LOCK:
        chats = _read_local_chats()
        chats[chat_id] = {"chat": item, "turns": []}
        write_json(LOCAL_CHAT_FILE, {"chats": chats})
    return chat_id, now


def append_turn(chat_id: str, turn: dict) -> None:
    now = _now()
    item = {
        "record_type": "turn",
        "created_at": now,
        **turn,
    }
    with _LOCAL_LOCK:
        chats = _read_local_chats()
        chat = chats.get(chat_id)
        if chat is None:
            raise KeyError(f"Chat not found: {chat_id}")
        chat["turns"].append(item)
        chat["chat"].update({
            "updated_at": now,
            "turn_count": len(chat["turns"]),
            "last_question": turn["question"][:300],
            "document_ids": turn["document_ids"],
            "model_ids": turn["model_ids"],
        })
        write_json(LOCAL_CHAT_FILE, {"chats": chats})


def list_chats(limit: int = 30) -> list[dict]:
    with _LOCAL_LOCK:
        chats = _read_local_chats()
    return sorted(
        (record["chat"] for record in chats.values()),
        key=lambda item: item["updated_at"],
        reverse=True,
    )[:limit]


def get_chat(chat_id: str) -> dict:
    with _LOCAL_LOCK:
        chat = _read_local_chats().get(chat_id)
    if chat is None:
        raise KeyError(f"Chat not found: {chat_id}")
    return {"chat": chat["chat"], "turns": chat["turns"]}


def recent_history(chat_id: str, limit: int = 4) -> list[dict]:
    with _LOCAL_LOCK:
        chat = _read_local_chats().get(chat_id)
    if chat is None:
        raise KeyError(f"Chat not found: {chat_id}")
    return chat["turns"][-limit:]


def _read_local_chats() -> dict[str, dict]:
    data = read_json(LOCAL_CHAT_FILE, {"chats": {}})
    chats = data.get("chats") if isinstance(data, dict) else None
    if not isinstance(chats, dict) or any(
        not isinstance(chat_id, str)
        or not isinstance(record, dict)
        or not isinstance(record.get("chat"), dict)
        or not isinstance(record.get("turns"), list)
        for chat_id, record in chats.items()
    ):
        raise ValueError(f"Local chat history is malformed: {LOCAL_CHAT_FILE}")
    return chats
