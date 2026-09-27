"""OpenAI-compatible provider: JSON extraction, schema validation, catalog-id guarding (fake client, no network)."""
import json
from types import SimpleNamespace

import pytest

from app.providers.llm.base import LLMError
from app.providers.llm.openai_compat import OpenAICompatLLMProvider, extract_json


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, reasoning_content="thinking…"))])


def fake_client(*replies):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(replies)))


CATALOG = [{"id": "cat_a", "trade_type": "Air Conditioning", "problem_name": "Water leakage", "source_row": 2},
           {"id": "cat_b", "trade_type": "Refrigerator", "problem_name": "Not cooling", "source_row": 8}]


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure, here you go: {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_interpret_validates_and_guards_catalog_ids():
    reply = json.dumps({"catalog_item_id": "cat_a", "confidence": 0.9, "alternatives": ["cat_b", "cat_zzz", "cat_a"],
                        "customer_name": "Alex", "contact_phone": "91234567", "location_hint": "Tampines",
                        "time_hint": None, "intent": "new_request", "language": "en", "payment_claimed": True})
    p = OpenAICompatLLMProvider(api_key="k", model="m", base_url="http://x/v1", client=fake_client(reply))
    out = p.interpret("aircon leaking", CATALOG, {"location_names": []})
    assert out.catalog_item_id == "cat_a" and out.alternatives == ["cat_b"]  # unknown id and duplicate dropped
    assert out.customer_name == "Alex" and out.payment_claimed is True
    sent = p.client.chat.completions.calls[0]
    assert sent["response_format"] == {"type": "json_object"} and sent["temperature"] == 0
    assert "cat_a" in sent["messages"][1]["content"] and "JSON schema" in sent["messages"][0]["content"]


def test_unknown_catalog_id_becomes_null_and_bad_json_raises():
    p = OpenAICompatLLMProvider(api_key="k", model="m", base_url="http://x/v1",
                                client=fake_client(json.dumps({"catalog_item_id": "cat_nope", "intent": "weird"}), "garbage"))
    out = p.interpret("?", CATALOG, {})
    assert out.catalog_item_id is None and out.intent == "other"
    with pytest.raises(LLMError):
        p.classify_complaint("late")


def test_requires_base_url_and_key():
    with pytest.raises(LLMError):
        OpenAICompatLLMProvider(api_key="", model="m", base_url="http://x/v1")
    with pytest.raises(LLMError):
        OpenAICompatLLMProvider(api_key="k", model="m", base_url="")
