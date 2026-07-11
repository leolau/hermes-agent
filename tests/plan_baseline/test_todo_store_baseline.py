"""Baseline invariants for the todo store.

Locks the ``TodoStore`` semantics FG-06 (task discovery + progress) extends:
the status vocabulary, replace/merge write semantics, and the rule that only
active (pending/in_progress) items are re-injected after context compression.
FG-06 must add scoping/discovery WITHOUT introducing a 4th task store or
drifting the status vocabulary.
"""

from tools.todo_tool import VALID_STATUSES, TodoStore


def test_status_vocabulary_is_stable():
    assert VALID_STATUSES == {"pending", "in_progress", "completed", "cancelled"}


def test_write_replace_semantics():
    store = TodoStore()
    store.write([{"id": "1", "content": "a", "status": "pending"}])
    store.write([{"id": "2", "content": "b", "status": "pending"}])
    ids = {t["id"] for t in store.read()}
    assert ids == {"2"}, "non-merge write replaces the whole list"


def test_write_merge_updates_and_appends():
    store = TodoStore()
    store.write([{"id": "1", "content": "a", "status": "pending"}])
    store.write(
        [
            {"id": "1", "status": "completed"},
            {"id": "2", "content": "b", "status": "pending"},
        ],
        merge=True,
    )
    by_id = {t["id"]: t for t in store.read()}
    assert by_id["1"]["status"] == "completed"
    assert by_id["1"]["content"] == "a"  # untouched field preserved
    assert by_id["2"]["content"] == "b"


def test_invalid_status_ignored_on_merge():
    store = TodoStore()
    store.write([{"id": "1", "content": "a", "status": "pending"}])
    store.write([{"id": "1", "status": "bogus"}], merge=True)
    assert store.read()[0]["status"] == "pending"


def test_injection_only_active_items():
    store = TodoStore()
    store.write(
        [
            {"id": "1", "content": "done", "status": "completed"},
            {"id": "2", "content": "todo", "status": "pending"},
            {"id": "3", "content": "doing", "status": "in_progress"},
            {"id": "4", "content": "dropped", "status": "cancelled"},
        ]
    )
    rendered = store.format_for_injection()
    assert rendered is not None
    assert "todo" in rendered and "doing" in rendered
    assert "done" not in rendered and "dropped" not in rendered


def test_injection_none_when_no_active_items():
    store = TodoStore()
    store.write([{"id": "1", "content": "done", "status": "completed"}])
    assert store.format_for_injection() is None
