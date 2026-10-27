from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderEntry:
    name: str
    display_name: str
    cls: type


class ProviderRegistry:
    """Decorator-based registry for provider adapters of a single kind.

    One ``ProviderRegistry`` instance manages one ABC family (embedding,
    reranker, llm, etc.). Adapters self-register via ``@registry.register(name)``
    and the factory looks them up by name.

    Aliases let an adapter be reachable under multiple names — primarily for
    backward compatibility when a provider string is renamed.

    See ``docs/PROVIDER_SPEC.md`` for the adapter authoring contract.
    """

    def __init__(self, kind: str):
        self._kind = kind
        self._registry: dict[str, ProviderEntry] = {}
        self._primary_names: list[str] = []

    def register(self, name: str, *aliases: str, display_name: str = ""):
        def decorator(cls: type) -> type:
            entry = ProviderEntry(
                name=name,
                display_name=display_name or name,
                cls=cls,
            )
            self._registry[name] = entry
            self._primary_names.append(name)
            for alias in aliases:
                self._registry[alias] = entry
            return cls

        return decorator

    def create(self, name: str, config: Any) -> Any:
        if name not in self._registry:
            raise ValueError(
                f"Unknown {self._kind} provider: {name!r}. "
                f"Available: {self._primary_names}"
            )
        return self._registry[name].cls(config)

    def list_primary(self) -> list[ProviderEntry]:
        """Primary entries in registration order (aliases excluded)."""
        return [self._registry[n] for n in self._primary_names]

    def get(self, name: str) -> ProviderEntry | None:
        """Get a registered entry by name, or None if not found."""
        return self._registry.get(name)

    def has(self, name: str) -> bool:
        return name in self._registry


embedding_registry = ProviderRegistry("embedding")
reranker_registry = ProviderRegistry("reranker")
llm_registry = ProviderRegistry("llm")
