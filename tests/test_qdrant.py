import pytest
from unittest.mock import MagicMock, patch
from src.db.qdrant import QdrantManager


@pytest.fixture
def mock_qdrant():
    with patch("src.db.qdrant.QdrantClient") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


def test_create_collection(mock_qdrant):
    mgr = QdrantManager(host="localhost", port=6333)
    mgr.create_collection("test_col", vector_size=512)
    mock_qdrant.create_collection.assert_called_once()


def test_list_collections(mock_qdrant):
    col1, col2 = MagicMock(), MagicMock()
    col1.name = "col1"
    col2.name = "col2"
    mock_qdrant.get_collections.return_value.collections = [col1, col2]
    mgr = QdrantManager(host="localhost", port=6333)
    result = mgr.list_collections()
    assert result == ["col1", "col2"]


def test_delete_collection(mock_qdrant):
    mgr = QdrantManager(host="localhost", port=6333)
    mgr.delete_collection("test_col")
    mock_qdrant.delete_collection.assert_called_once_with(collection_name="test_col")


def test_upsert_points(mock_qdrant):
    mgr = QdrantManager(host="localhost", port=6333)
    mgr.upsert_points(
        collection="test_col",
        ids=["id1"],
        vectors=[[0.1] * 512],
        payloads=[{"text": "hello"}],
    )
    mock_qdrant.upsert.assert_called_once()


def test_search(mock_qdrant):
    mock_point = MagicMock()
    mock_point.id = "id1"
    mock_point.score = 0.95
    mock_point.payload = {"text": "hello"}
    mock_result = MagicMock()
    mock_result.points = [mock_point]
    mock_qdrant.query_points.return_value = mock_result

    mgr = QdrantManager(host="localhost", port=6333)
    results = mgr.search("test_col", query_vector=[0.1] * 512, top_k=5)
    assert len(results) == 1
    assert results[0]["id"] == "id1"
    assert results[0]["score"] == 0.95
