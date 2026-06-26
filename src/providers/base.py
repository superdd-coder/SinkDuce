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
    def generate(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None, response_format: dict | None = None, thinking: bool | None = None) -> str: ...

    @abstractmethod
    def generate_stream(self, prompt: str, system: str = "", temperature: float | None = None, max_tokens: int | None = None, response_format: dict | None = None, thinking: bool | None = None): ...

    def batch_submit(self, requests: list[dict]) -> str:
        """Submit a batch of generation requests. Returns a batch_id for polling.

        Each request dict: {"prompt": str, "system": str, "temperature": float | None, "max_tokens": int | None}
        """
        raise NotImplementedError("This LLM provider does not support batch generation")

    def batch_poll(self, batch_id: str) -> list[str] | None:
        """Poll batch status by batch_id. Returns list of generated texts when complete, None if still running."""
        raise NotImplementedError("This LLM provider does not support batch generation")

    def describe_image(self, image_base64: str, image_mime: str = "image/png", prompt: str = "") -> str:
        """Generate a text description of an image using Vision API.

        Default implementation raises NotImplementedError. Override in providers
        that support vision/image input (e.g., GPT-4o, Claude).

        Args:
            image_base64: Base64-encoded image data
            image_mime: MIME type of the image
            prompt: Custom prompt to send with the image (provider may use a default if empty)
        """
        raise NotImplementedError("This LLM provider does not support image description")
