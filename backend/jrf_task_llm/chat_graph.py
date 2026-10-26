from typing import TypedDict
import logging

from langgraph.graph import END, START, StateGraph

from . import chat_store, local_model, vector_store
from .config import DEFAULT_TOP_K, MAX_TOP_K

LOGGER = logging.getLogger(__name__)


class ChatState(TypedDict, total=False):
    chat_id: str
    question: str
    document_ids: list[str]
    model_ids: list[str]
    top_k: int
    history: list[dict]
    retrieved: list[dict]
    answers: list[dict]
    result: dict


def retrieve_context(state: ChatState) -> dict:
    history = chat_store.recent_history(state["chat_id"])
    retrieved = vector_store.search_chunks(
        state["question"],
        state["document_ids"],
        state["top_k"],
    )
    if not retrieved:
        raise ValueError(
            "No indexed chunks found for the selected documents. Index them first."
        )
    return {"history": history, "retrieved": retrieved}


def answer_question(state: ChatState) -> dict:
    answers = []
    for model_id in state["model_ids"]:
        try:
            answers.append(local_model.answer_question(
                model_id,
                state["question"],
                state["retrieved"],
                state["history"],
            ))
        except Exception as exc:
            LOGGER.exception("Local model inference failed: model_id=%s", model_id)
            answers.append({
                "model_id": model_id,
                "error": str(exc)[:500],
            })
    return {"answers": answers}


def persist_result(state: ChatState) -> dict:
    retrieved = state["retrieved"]
    answers = state["answers"]
    document_ids = state["document_ids"]
    model_ids = state["model_ids"]
    used_documents = sorted({item["document_id"] for item in retrieved})
    successful_answers = [answer for answer in answers if not answer.get("error")]
    status = (
        "failed" if not successful_answers
        else "partial" if len(successful_answers) != len(answers)
        else "complete"
    )

    chat_store.append_turn(state["chat_id"], {
        "question": state["question"],
        "document_ids": document_ids,
        "documents_fetched": used_documents,
        "model_ids": model_ids,
        "answers": answers,
        "retrieved_sources": [
            {
                "document_id": item["document_id"],
                "filename": item["filename"],
                "page": item["page"],
                "bbox": item.get("bbox"),
                "score": item.get("score"),
                "text": item["text"],
            }
            for item in retrieved
        ],
    })
    return {
        "result": {
            "chat_id": state["chat_id"],
            "question": state["question"],
            "document_ids": document_ids,
            "documents_fetched": used_documents,
            "status": status,
            "answers": answers,
        }
    }


def build_chat_graph():
    graph = StateGraph(ChatState)
    graph.add_node("retrieve", retrieve_context)
    graph.add_node("answer", answer_question)
    graph.add_node("history_and_logger", persist_result)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", "history_and_logger")
    graph.add_edge("history_and_logger", END)
    return graph.compile()


CHAT_GRAPH = build_chat_graph()


def run_chat(chat_id: str, payload: dict) -> dict:
    top_k = min(MAX_TOP_K, max(1, int(payload.get("top_k", DEFAULT_TOP_K))))
    state = CHAT_GRAPH.invoke({
        "chat_id": chat_id,
        "question": payload["question"],
        "document_ids": payload["document_ids"],
        "model_ids": payload["model_ids"],
        "top_k": top_k,
    })
    return state["result"]
