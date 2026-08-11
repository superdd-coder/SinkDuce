from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.config import TranscriptionProviderConfig
from src.meeting.models import TranscriptSegment, TranscriptionResult
from src.meeting.transcription.base import FileTranscriptionProvider
from src.meeting.transcription.registry import file_transcription_registry

try:
    import dashscope
    from dashscope.audio.asr import Transcription, VocabularyService

    _HAS_DASHSCOPE = True
except ImportError:
    _HAS_DASHSCOPE = False

logger = logging.getLogger(__name__)

_DEFAULT_HTTP_URL = "https://dashscope.aliyuncs.com/api/v1"
# Selectable file ASR models (provider config.model).
MODEL_FUN_ASR = "fun-asr"
MODEL_QWEN_FILETRANS = "qwen-audio-3.0-asr-flash-filetrans"
ALLOWED_FILE_MODELS = (MODEL_FUN_ASR, MODEL_QWEN_FILETRANS)
_DEFAULT_FILE_MODEL = MODEL_FUN_ASR
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception indicates a rate limit (HTTP 429)."""
    msg = str(exc).lower()
    return "429" in msg or "throttling" in msg or "ratequota" in msg or "rate_quota" in msg


def _retry_with_backoff(fn, description: str = "API call"):
    """Call fn() with exponential backoff on rate limit errors."""
    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit_error(exc) or attempt >= _MAX_RETRIES:
                raise
            delay = _RETRY_BASE_DELAY ** attempt
            logger.warning(
                "[DashScope] Rate limit on %s (attempt %d/%d), retrying in %.1fs...",
                description, attempt, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _require_dashscope() -> None:
    if not _HAS_DASHSCOPE:
        raise ImportError(
            "dashscope package is required for DashScope transcription. "
            "Install it with: pip install dashscope"
        )


def resolve_file_model(model: str | None) -> str:
    """Normalize provider config model to an allowed DashScope file ASR id."""
    m = (model or "").strip()
    if m in ALLOWED_FILE_MODELS:
        return m
    if m:
        logger.warning(
            "[DashScope] Unknown file model %r; using default %s. Allowed: %s",
            m, _DEFAULT_FILE_MODEL, ", ".join(ALLOWED_FILE_MODELS),
        )
    return _DEFAULT_FILE_MODEL


def _format_dashscope_task_failure(task_id: str, output: dict, status_response: Any = None) -> str:
    """Build a human-readable error from a FAILED DashScope transcription task."""
    parts = [f"Transcription task {task_id} FAILED"]
    code = output.get("code")
    message = output.get("message")
    if code:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    if status_response is not None:
        for attr in ("code", "message", "status_code"):
            val = getattr(status_response, attr, None)
            if val is None:
                continue
            if attr == "code" and code:
                continue
            if attr == "message" and message:
                continue
            parts.append(f"{attr}={val}")
    results = output.get("results") or []
    for i, entry in enumerate(results):
        if not isinstance(entry, dict):
            continue
        sub_status = entry.get("subtask_status") or entry.get("task_status")
        sub_code = entry.get("code")
        sub_msg = entry.get("message")
        if sub_status in (None, "SUCCEEDED") and not sub_code:
            continue
        bits = [f"result[{i}]"]
        if sub_status:
            bits.append(f"subtask_status={sub_status}")
        if sub_code:
            bits.append(f"code={sub_code}")
        if sub_msg:
            bits.append(f"message={sub_msg}")
        parts.append(" ".join(bits))
    metrics = output.get("task_metrics")
    if metrics:
        parts.append(f"metrics={metrics}")
    return " | ".join(parts)


def _format_dashscope_subtask_failures(task_id: str, output: dict) -> str | None:
    """If task is SUCCEEDED but all file subtasks failed, return an error string."""
    results = output.get("results") or []
    if not results:
        return None
    failures = []
    ok = 0
    for i, entry in enumerate(results):
        if not isinstance(entry, dict):
            continue
        if entry.get("transcription_url") and entry.get("subtask_status") != "FAILED":
            ok += 1
            continue
        if entry.get("subtask_status") == "FAILED" or entry.get("code"):
            failures.append(
                f"result[{i}] code={entry.get('code')} message={entry.get('message')}"
            )
    if failures and ok == 0:
        return (
            f"Transcription task {task_id} SUCCEEDED at task level but all "
            f"file subtasks failed: {'; '.join(failures)}"
        )
    return None


def _build_instant_vocabulary(hot_words: list | None) -> dict[str, int] | None:
    """Convert HotWordItem list to DashScope instant vocabulary dict (Qwen).

    Instant hot words are ``{text: weight}`` with weight in [1, 5] (or 50 for
    super-hot). App weights are 1–10; map them into the 1–5 range used by the API.
    """
    if not hot_words:
        return None
    vocab: dict[str, int] = {}
    for hw in hot_words:
        text = hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
        weight = hw.get("weight", 4) if isinstance(hw, dict) else getattr(hw, "weight", 4)
        if not text:
            continue
        vocab[str(text)] = min(5, max(1, int(weight) // 2 + 1))
    return vocab if vocab else None


def _build_precompiled_vocabulary_items(hot_words: list | None) -> list | None:
    """Convert HotWordItem list to DashScope VocabularyService item list (Fun-ASR)."""
    if not hot_words:
        return None
    items = []
    for hw in hot_words:
        text = hw.get("text", "") if isinstance(hw, dict) else getattr(hw, "text", "")
        weight = hw.get("weight", 4) if isinstance(hw, dict) else getattr(hw, "weight", 4)
        lang = hw.get("lang", "") if isinstance(hw, dict) else getattr(hw, "lang", "")
        if not text:
            continue
        item: dict[str, Any] = {
            "text": text,
            "weight": min(5, max(1, int(weight) // 2 + 1)),
        }
        if lang:
            item["lang"] = lang
        items.append(item)
    return items if items else None


@file_transcription_registry.register(
    "dashscope_funasr",
    display_name="DashScope ASR (file)",
)
class DashScopeFileTranscription(FileTranscriptionProvider):
    """DashScope file transcription (Fun-ASR or Qwen-Audio filetrans).

    Uploads local audio files to DashScope OSS, then uses the batch
    Transcription API with speaker diarization support.
    For HTTP(S) URLs, uses the async Transcription API directly.

    Model is selected via ``config.model``:
      - ``fun-asr`` (default) — precompiled hot words via VocabularyService
      - ``qwen-audio-3.0-asr-flash-filetrans`` — instant ``vocabulary`` dict
    """

    supports_hot_words = True
    SUPPORTED_LANGUAGE_HINTS = [
        {"code": "auto", "label": "Auto"},
        {"code": "zh", "label": "Chinese"},
        {"code": "en", "label": "English"},
        {"code": "ja", "label": "Japanese"},
        {"code": "ko", "label": "Korean"},
        {"code": "ms", "label": "Malay"},
        {"code": "th", "label": "Thai"},
        {"code": "id", "label": "Indonesian"},
    ]
    SUPPORTED_MODELS = [
        {"value": MODEL_FUN_ASR, "label": "fun-asr (FunASR cloud)"},
        {"value": MODEL_QWEN_FILETRANS, "label": "qwen-audio-3.0-asr-flash-filetrans"},
    ]

    def __init__(self, config: TranscriptionProviderConfig):
        _require_dashscope()
        self._api_key = config.api_key
        self._model = resolve_file_model(config.model)
        self._http_url = _DEFAULT_HTTP_URL
        logger.info("[DashScope] File transcription model=%s", self._model)

    def _uses_precompiled_vocabulary(self) -> bool:
        """Fun-ASR uses VocabularyService; Qwen uses instant vocabulary dict."""
        return self._model == MODEL_FUN_ASR

    def _create_vocabulary(self, hot_words: list) -> str | None:
        """Create a cloud-side hot words vocabulary and return its ID (Fun-ASR).

        DashScope requires creating a vocabulary first, then passing its ID
        as ``vocabulary_id`` to the transcription call. Returns None on failure.
        """
        vocabulary = _build_precompiled_vocabulary_items(hot_words)
        if not vocabulary:
            return None

        service = VocabularyService(api_key=self._api_key)
        prefix = f"tr{int(time.time() * 1000) % 10000000:07d}"  # max 10 chars
        logger.info(
            "[DashScope] Creating vocabulary: prefix=%s model=%s words=%s",
            prefix, self._model, vocabulary,
        )
        try:
            vocab_id = service.create_vocabulary(
                target_model=self._model,
                prefix=prefix,
                vocabulary=vocabulary,
            )
        except Exception as exc:
            logger.error("[DashScope] Failed to create vocabulary: %s", exc)
            return None

        if not vocab_id:
            logger.error("[DashScope] Empty vocabulary_id returned")
            return None

        for _ in range(30):
            try:
                full_response = service.query_vocabulary(vocab_id)
                status = full_response[0] if isinstance(full_response, list) else full_response
                logger.info("[DashScope] Query vocab %s response: %s", vocab_id, full_response)
                if isinstance(status, dict) and status.get("status") == "OK":
                    time.sleep(2)
                    logger.info(
                        "[DashScope] Vocabulary %s ready with %d hot words",
                        vocab_id, len(vocabulary),
                    )
                    return vocab_id
            except Exception as exc:
                logger.warning("[DashScope] Query vocabulary %s failed: %s", vocab_id, exc)
            time.sleep(0.5)

        logger.error("[DashScope] Vocabulary %s timed out waiting for OK status", vocab_id)
        self._delete_vocabulary(vocab_id, self._api_key)
        return None

    @staticmethod
    def _delete_vocabulary(vocab_id: str, api_key: str | None = None) -> None:
        """Delete a cloud-side hot words vocabulary to free quota (max 10 per account)."""
        try:
            VocabularyService(api_key=api_key).delete_vocabulary(vocab_id)
            logger.info("[DashScope] Deleted vocabulary %s", vocab_id)
        except Exception as exc:
            logger.warning("[DashScope] Failed to delete vocabulary %s: %s", vocab_id, exc)

    async def transcribe(
        self,
        file_path: str,
        language_hints: list[str] | None = None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file.

        For local files, uploads to DashScope OSS then uses batch Transcription API.
        For HTTP(S) URLs, uses the async Transcription API directly.
        Both paths support speaker diarization.
        """
        if file_path.startswith(("http://", "https://")):
            result = await asyncio.to_thread(
                self._transcribe_from_url, file_path, language_hints, hot_words
            )
        else:
            result = await asyncio.to_thread(
                self._transcribe_local_file, file_path, language_hints, hot_words
            )
        return result

    def _transcribe_local_file(
        self,
        file_path: str,
        language_hints: list[str] | None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        """Upload local file to DashScope OSS, then use batch Transcription API."""
        from dashscope import Files
        dashscope.api_key = self._api_key
        file_id = None

        try:
            logger.info("Uploading local file to DashScope: %s", file_path)
            upload_result = _retry_with_backoff(
                lambda: Files.upload(file_path=file_path, purpose="inference"),
                description="file upload",
            )
            if upload_result.status_code != 200:
                raise RuntimeError(f"DashScope file upload failed: {upload_result}")
            file_id = upload_result.output["uploaded_files"][0]["file_id"]
            logger.info("File uploaded, file_id=%s", file_id)

            file_info = Files.get(file_id=file_id)
            oss_url = file_info.output["url"]
            logger.info("Got OSS URL for file")

            return self._transcribe_from_url(oss_url, language_hints, hot_words)
        finally:
            if file_id:
                try:
                    Files.delete(file_id=file_id)
                    logger.info("Deleted uploaded file from DashScope: %s", file_id)
                except Exception:
                    logger.warning("Failed to delete uploaded file: %s", file_id)

    def _transcribe_from_url(
        self,
        file_url: str,
        language_hints: list[str] | None,
        hot_words: list | None = None,
    ) -> TranscriptionResult:
        """Transcribe via async Transcription API (requires public URL)."""
        dashscope.api_key = self._api_key
        dashscope.base_http_api_url = self._http_url

        vocab_id: str | None = None
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "file_urls": [file_url],
                "diarization_enabled": True,
            }
            if language_hints:
                kwargs["language_hints"] = language_hints

            if hot_words:
                if self._uses_precompiled_vocabulary():
                    vocab_id = self._create_vocabulary(hot_words)
                    if vocab_id:
                        kwargs["vocabulary_id"] = vocab_id
                        logger.info(
                            "[DashScope] Using precompiled vocabulary_id=%s (model=%s)",
                            vocab_id, self._model,
                        )
                else:
                    vocabulary = _build_instant_vocabulary(hot_words)
                    if vocabulary:
                        kwargs["vocabulary"] = vocabulary
                        logger.info(
                            "[DashScope] Using instant vocabulary (%d terms, model=%s)",
                            len(vocabulary), self._model,
                        )

            logger.info(
                "Submitting URL-based transcription: model=%s url=%s",
                self._model, file_url,
            )
            task_response = _retry_with_backoff(
                lambda: Transcription.async_call(**kwargs),
                description="transcription submit",
            )
            if task_response.output is None:
                raise RuntimeError(
                    f"Transcription async_call returned null output. "
                    f"status_code={task_response.status_code} code={task_response.code} "
                    f"message={task_response.message} kwargs_keys={list(kwargs.keys())}"
                )
            task_id = task_response.output.get("task_id")
            logger.info("Transcription task submitted: task_id=%s", task_id)

            logger.info(
                "Waiting for transcription task %s to complete (polling every 5s)...",
                task_id,
            )
            while True:
                time.sleep(5)
                status_response = Transcription.fetch(task=task_id)
                if status_response.output is None:
                    logger.error(
                        "Transcription task %s returned null output: %s",
                        task_id, status_response,
                    )
                    raise RuntimeError(f"Transcription task {task_id} returned null output")
                output = status_response.output
                if not isinstance(output, dict):
                    output = dict(output) if hasattr(output, "items") else {"raw": output}
                task_status = output.get("task_status")
                logger.info("Transcription task %s status: %s", task_id, task_status)
                if task_status == "FAILED":
                    detail = _format_dashscope_task_failure(task_id, output, status_response)
                    logger.error("[DashScope] Transcription FAILED: %s", detail)
                    raise RuntimeError(detail)
                if task_status == "SUCCEEDED":
                    fail_detail = _format_dashscope_subtask_failures(task_id, output)
                    if fail_detail:
                        logger.error("[DashScope] Transcription subtask FAILED: %s", fail_detail)
                        raise RuntimeError(fail_detail)
                    break

            logger.info(
                "Transcription task %s completed, output keys: %s",
                task_id,
                list(output.keys()) if isinstance(output, dict) else type(output),
            )

            transcription_urls = output.get("results", []) if isinstance(output, dict) else []
            segments: list[TranscriptSegment] = []

            for entry in transcription_urls:
                url = entry.get("transcription_url")
                if not url:
                    logger.warning("No transcription_url in entry: %s", entry)
                    continue
                try:
                    parsed = self._fetch_and_parse_segments(url)
                    logger.info("Fetched %d segments from %s", len(parsed), url[:80])
                    segments.extend(parsed)
                except Exception:
                    logger.warning(
                        "Failed to fetch transcription result from %s", url, exc_info=True,
                    )

            full_text = " ".join(s.text for s in segments)
            logger.info(
                "URL transcription done: %d segments, %d chars",
                len(segments), len(full_text),
            )
            return TranscriptionResult(text=full_text, segments=segments)
        finally:
            if vocab_id:
                self._delete_vocabulary(vocab_id, self._api_key)

    @staticmethod
    def _fetch_and_parse_segments(url: str) -> list[TranscriptSegment]:
        """Download the transcription JSON from DashScope and parse segments.

        DashScope returns ``transcripts[]``, each containing a ``sentences``
        array with ``text``, ``begin_time``, ``end_time``, and ``speaker_id``.
        """
        resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        segments: list[TranscriptSegment] = []
        for transcript in data.get("transcripts", []):
            sentences = transcript.get("sentences", [])
            for item in sentences:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                segments.append(
                    TranscriptSegment(
                        start=float(item.get("begin_time", 0)) / 1000.0,  # ms -> s
                        end=float(item.get("end_time", 0)) / 1000.0,
                        text=text,
                        speaker_id=(
                            str(item.get("speaker_id"))
                            if item.get("speaker_id") is not None
                            else None
                        ),
                    )
                )
        return segments
