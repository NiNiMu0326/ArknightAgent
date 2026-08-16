"""
Tests for backend.quick_questions: capability template pools.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.quick_questions import (
    STRUCTURED_TEMPLATES,
    PRTS_MCP_TEMPLATES,
    RAG_FALLBACK_TEMPLATES,
    pick_template,
    pick_rag_question,
)


class TestTemplatePools:
    def test_four_categories_have_templates(self):
        assert len(STRUCTURED_TEMPLATES) >= 3
        assert len(PRTS_MCP_TEMPLATES) >= 3
        assert len(RAG_FALLBACK_TEMPLATES) >= 1

    def test_all_templates_have_required_fields(self):
        for template in STRUCTURED_TEMPLATES + PRTS_MCP_TEMPLATES + RAG_FALLBACK_TEMPLATES:
            assert template["label"]
            assert template["question"]
            assert template["category"] in {"rag", "graph", "structured", "prts_mcp"}


class TestPickTemplate:
    def test_prefers_non_excluded_label(self, monkeypatch):
        templates = [
            {"label": "a", "question": "qa", "category": "structured"},
            {"label": "b", "question": "qb", "category": "structured"},
        ]
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        picked = pick_template(templates, {"a"})
        assert picked["label"] == "b"

    def test_falls_back_when_all_excluded(self, monkeypatch):
        templates = [{"label": "a", "question": "qa", "category": "structured"}]
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        picked = pick_template(templates, {"a"})
        assert picked["label"] == "a"


class TestPickRagQuestion:
    def test_returns_rag_question_with_data(self, monkeypatch):
        monkeypatch.setattr("backend.quick_questions.random.shuffle", lambda seq: None)
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        q = pick_rag_question(["阿米娅"], [], [], [], set())
        assert q["category"] == "rag"
        assert q["question"] == "阿米娅的技能是什么"

    def test_returns_fallback_without_data(self):
        q = pick_rag_question([], [], [], [], set())
        assert q["category"] == "rag"
        assert q["question"]
