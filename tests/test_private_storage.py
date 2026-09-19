import base64
import json
from unittest.mock import Mock
import pytest
from storage.portfolios import GitHubPortfolioStore, StorageError, ConflictError


def content(obj):
    return {"content": base64.b64encode(json.dumps(obj).encode()).decode()}


def test_atomic_commit_and_nonforced_update():
    store = GitHubPortfolioStore("test-placeholder")
    store._api = Mock(side_effect=[{"private": True}, {"object": {"sha": "head"}},
        content({"portfolios": []}), {"tree": {"sha": "tree-before"}}, {"sha": "tree-after"}, {"sha": "commit"}, {}])
    p = {"id": "a"*32, "name": "Teste", "created_at": "2026-01-01", "revision": 1}
    store.save(p, {"type": "creation"})
    tree_call = store._api.call_args_list[4]
    assert tree_call.kwargs["json"]["base_tree"] == "tree-before"
    assert {x["path"].split('/')[0] for x in tree_call.kwargs["json"]["tree"]} == {"index.json", "portfolios", "history"}
    assert store._api.call_args.kwargs["json"] == {"sha": "commit", "force": False}


def test_stale_revision_does_not_write():
    store = GitHubPortfolioStore("test-placeholder")
    store._api = Mock(side_effect=[{"private": True}, {"object": {"sha": "head"}},
        content({"portfolios": [{"id": "a"*32}]}), content({"revision": 3})])
    with pytest.raises(ConflictError):
        store.save({"id": "a"*32, "revision": 2}, {}, expected_revision=1)
    assert all(x.args[0] == "GET" for x in store._api.call_args_list)


def test_public_repository_rejected():
    store = GitHubPortfolioStore("test-placeholder")
    store._api = Mock(return_value={"private": False})
    with pytest.raises(StorageError, match="privado"):
        store.list_portfolios()


def test_transport_error_does_not_expose_token():
    import requests
    session = Mock()
    session.request.side_effect = requests.RequestException("secret-token")
    store = GitHubPortfolioStore("secret-token", session=session)
    with pytest.raises(StorageError) as error:
        store.list_portfolios()
    assert "secret-token" not in str(error.value)
