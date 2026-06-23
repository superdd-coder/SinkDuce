from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...


class RerankerProvider(ABC):
    @abstractmethod
    def rerank(
        self, query: str, documents: list[str], top_k: int = 5
    ) -> list[tuple[int, float]]: ...


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None) -> str: ...

    @abstractmethod
    def generate_stream(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None): ...

    def describe_image(self, image_base64: str, image_mime: str = "image/png") -> str:
        """Generate a text description of an image using Vision API.

        Default implementation raises NotImplementedError. Override in providers
        that support vision/image input (e.g., GPT-4o, Claude).
        """
        raise NotImplementedError("This LLM provider does not support image description")
