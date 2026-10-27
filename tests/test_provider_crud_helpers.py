"""Shared provider-list CRUD helpers (Settings config routes)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Dummy:
    id: str = ""
    name: str = ""
    is_default: bool = False
    dimensions: int | None = None

    def model_copy(self):
        return Dummy(
            id=self.id,
            name=self.name,
            is_default=self.is_default,
            dimensions=self.dimensions,
        )


def test_add_assigns_id_and_exclusive_default():
    from src.api.routes.config import _add_to_provider_list

    a = Dummy(id="a", is_default=True)
    lst = [a]
    added = _add_to_provider_list(lst, Dummy(name="b", is_default=True), flag="is_default")
    assert added.id
    assert added.is_default is True
    assert a.is_default is False
    assert lst[-1] is added


def test_add_first_item_becomes_default():
    from src.api.routes.config import _add_to_provider_list

    lst: list[Dummy] = []
    added = _add_to_provider_list(lst, Dummy(name="only"), flag="is_default")
    assert added.is_default is True


def test_patch_int_and_exclusive_flag():
    from src.api.routes.config import _apply_provider_update

    p = Dummy(id="x", dimensions=8)
    lst = [p, Dummy(id="y", is_default=True)]
    found = _apply_provider_update(
        lst,
        "x",
        {"dimensions": "32", "is_default": True},
        int_fields={"dimensions"},
        bool_fields={"is_default"},
        exclusive_flag="is_default",
    )
    assert found is p
    assert p.dimensions == 32
    assert p.is_default is True
    assert lst[1].is_default is False


def test_delete_promotes_default_when_asked():
    from src.api.routes.config import _remove_provider

    a = Dummy(id="a", is_default=True)
    b = Dummy(id="b")
    removed, rest = _remove_provider([a, b], "a", exclusive_flag="is_default", promote=True)
    assert removed is a
    assert rest[0] is b
    assert b.is_default is True


def test_delete_missing_returns_none():
    from src.api.routes.config import _remove_provider

    removed, rest = _remove_provider([Dummy(id="a")], "nope")
    assert removed is None
    assert len(rest) == 1
