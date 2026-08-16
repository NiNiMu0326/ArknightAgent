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
    make_artwork_question,
    make_stage_enemies_question,
    make_stage_item_question,
    pick_unique_questions,
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

    def test_dynamic_prts_templates_fill_real_names(self):
        assert make_artwork_question("阿米娅")["label"] == "阿米娅立绘"
        assert "阿米娅" in make_artwork_question("阿米娅")["question"]
        assert make_stage_enemies_question("1-7")["label"] == "1-7出怪顺序"
        assert "1-7" in make_stage_enemies_question("1-7")["question"]
        assert make_stage_item_question("CE-5")["label"] == "CE-5材料掉落"
        assert "CE-5" in make_stage_item_question("CE-5")["question"]

    def test_pick_unique_questions_respects_count_and_dedup(self, monkeypatch):
        def make_question(name):
            return {"label": f"{name}立绘", "question": f"{name}有哪些立绘？", "category": "prts_mcp"}

        choices = iter(["阿米娅", "陈", "陈"])
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: next(choices))
        questions = pick_unique_questions(["阿米娅", "陈"], make_question, {"阿米娅立绘"}, 1)
        assert len(questions) == 1
        assert questions[0]["label"] == "陈立绘"


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

    def test_kind_filter_returns_requested_type(self, monkeypatch):
        monkeypatch.setattr("backend.quick_questions.random.shuffle", lambda seq: None)
        monkeypatch.setattr("backend.quick_questions.random.choice", lambda seq: seq[0])
        q = pick_rag_question(["阿米娅"], ["某故事"], ["源石虫"], [("银灰", ["老板"])], set(), kind="enemy")
        assert q["type"] == "enemy"
        assert q["category"] == "rag"
        assert "源石虫" in q["question"]

    def test_kind_filter_uses_same_kind_fallback_when_no_data(self):
        q = pick_rag_question([], [], [], [], set(), kind="alias")
        assert q["type"] == "alias"
        assert q["category"] == "rag"
        assert q["label"] == "银灰别名"
