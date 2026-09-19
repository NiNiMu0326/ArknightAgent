"""
Tests for backend.rag.graphrag.query: get_graph_builder singleton.
Usage: cd test && python -m pytest test_graphrag_query.py -v
"""
import sys
import pytest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.rag.graphrag import query


# ============================================================
# get_graph_builder tests
# ============================================================

class TestGetGraphBuilder:
    """Test the singleton GraphBuilder factory function."""

    def test_returns_graph_builder(self):
        """get_graph_builder should return a GraphBuilder instance."""
        # Reset singleton for isolated test
        query._graph_builder_instance = None

        with mock.patch.object(query.GraphBuilder, 'build') as mock_build:
            mock_build.return_value = mock.MagicMock()
            gb = query.get_graph_builder()
            assert gb is not None
            mock_build.assert_called_once()

    def test_singleton_behavior(self):
        """Multiple calls should return the same instance."""
        query._graph_builder_instance = None

        with mock.patch.object(query.GraphBuilder, 'build') as mock_build:
            mock_build.return_value = mock.MagicMock()
            gb1 = query.get_graph_builder()
            gb2 = query.get_graph_builder()
            assert gb1 is gb2
            # build should only be called once
            mock_build.assert_called_once()

    def test_filenotfound_does_not_crash(self):
        """Missing entity_relations.json warns but does not crash.

        The contract is ``None``, not a half-built builder: ``builder.graph`` is
        an empty DiGraph in that case, and returning it made callers' null-guards
        useless and turned "graph never loaded" into a silent "no relations".
        """
        query._graph_builder_instance = None

        with mock.patch.object(
            query.GraphBuilder, 'build', side_effect=FileNotFoundError
        ) as mock_build:
            with pytest.warns(UserWarning, match="entity_relations.json not found"):
                gb = query.get_graph_builder()
            assert gb is None
            # A failed build must not be cached as the singleton
            assert query._graph_builder_instance is None
            # ... so the next call retries instead of being permanently degraded
            with pytest.warns(UserWarning, match="entity_relations.json not found"):
                assert query.get_graph_builder() is None
            assert mock_build.call_count == 2

    def test_generic_exception_does_not_crash(self):
        """Other build errors should warn but not crash, and return None."""
        query._graph_builder_instance = None

        with mock.patch.object(
            query.GraphBuilder, 'build', side_effect=RuntimeError("test error")
        ) as mock_build:
            with pytest.warns(UserWarning, match="Failed to build GraphRAG"):
                gb = query.get_graph_builder()
            assert gb is None
            assert query._graph_builder_instance is None
            with pytest.warns(UserWarning, match="Failed to build GraphRAG"):
                assert query.get_graph_builder() is None
            assert mock_build.call_count == 2

    def test_thread_safety_lock_exists(self):
        """The module should have a threading.Lock for thread safety."""
        assert hasattr(query, '_graph_builder_lock')
        import threading
        assert isinstance(query._graph_builder_lock, type(threading.Lock()))

    def test_global_variable_exists(self):
        """Module-level singleton variable should exist."""
        assert hasattr(query, '_graph_builder_instance')
