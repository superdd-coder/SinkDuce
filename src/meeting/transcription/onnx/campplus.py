"""CAM++ speaker embedding (ONNX) + simple clustering for diarization.

Embedding model: 3D-Speaker / FunASR campplus style — fixed-dim vectors per
VAD segment, then cosine agglomerative clustering (no pyannote).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


def _compute_fbank_80(
    waveform: np.ndarray,
    sample_rate: int = 16000,
) -> np.ndarray:
    """80-dim Kaldi fbank, shape ``[num_frames, 80]`` float32.

    Required by public CAM++ ONNX packs that take input ``feats`` with layout
    ``[batch, time, 80]`` (not raw PCM).
    """
    import kaldi_native_fbank as knf

    wav = np.asarray(waveform, dtype=np.float32).reshape(-1)
    if sample_rate != 16000 and wav.size > 0:
        # linear resample to 16 kHz (same approach as file pipeline)
        duration = len(wav) / float(sample_rate)
        n = max(1, int(duration * 16000))
        x_old = np.linspace(0.0, 1.0, num=len(wav), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
        wav = np.interp(x_new, x_old, wav).astype(np.float32)
        sample_rate = 16000

    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = float(sample_rate)
    opts.frame_opts.dither = 0.0
    opts.frame_opts.snip_edges = False
    opts.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(opts)
    # Prefer the float32 buffer — .tolist() copies every sample into a
    # Python float and dominates CAM++ time on long VAD segments.
    try:
        fbank.accept_waveform(sample_rate, wav)
    except TypeError:
        fbank.accept_waveform(sample_rate, wav.tolist())
    fbank.input_finished()
    n_frames = int(fbank.num_frames_ready)
    if n_frames <= 0:
        return np.zeros((0, 80), dtype=np.float32)
    return np.stack(
        [np.asarray(fbank.get_frame(i), dtype=np.float32) for i in range(n_frames)],
        axis=0,
    )


class CampplusOnnxEmbedder:
    """Thin ORT wrapper around a CAM++ / 3D-Speaker embedding onnx."""

    def __init__(self, model_path: Path, *, num_threads: int = 4):
        import onnxruntime as ort

        from src.meeting.transcription.onnx.threads import apply_session_options

        opts = ort.SessionOptions()
        apply_session_options(opts, num_threads=num_threads, arena=True)
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name
        in_shape = self._session.get_inputs()[0].shape
        outs = self._session.get_outputs()
        self._output_name = outs[0].name
        # Official/community packs: feats [batch, time, 80] or [batch, 80, time]
        self._feats_time_major = True  # [B, T, 80]
        if isinstance(in_shape, (list, tuple)) and len(in_shape) == 3:
            # static 80 on dim1 → [B, 80, T]
            if in_shape[1] == 80:
                self._feats_time_major = False
            elif in_shape[2] == 80:
                self._feats_time_major = True
        logger.info(
            "CAM++ ONNX loaded: %s in=%s shape=%s time_major=%s out=%s",
            model_path.name,
            self._input_name,
            in_shape,
            self._feats_time_major,
            self._output_name,
        )

    def _zero_emb(self) -> np.ndarray:
        try:
            out = self._session.get_outputs()[0]
            # shape like [batch, 192]
            dim = 192
            sh = out.shape
            if isinstance(sh, (list, tuple)) and len(sh) >= 2 and isinstance(sh[-1], int):
                dim = int(sh[-1])
        except Exception:
            dim = 192
        return np.zeros(dim, dtype=np.float32)

    def embed(self, waveform: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        """Return L2-normalized embedding for mono float32 waveform.

        Converts PCM → 80-dim fbank, then runs ORT. Passing raw waveform fails
        on packs that declare ``feats: [batch, time, 80]`` and previously led to
        all-zero embeddings (every VAD segment became a unique speaker).
        """
        wav = np.asarray(waveform, dtype=np.float32).reshape(-1)
        if wav.size < sample_rate // 10:
            return self._zero_emb()

        try:
            feats = _compute_fbank_80(wav, sample_rate=sample_rate)
        except Exception:
            logger.warning("CAM++ fbank extraction failed", exc_info=True)
            return self._zero_emb()

        if feats.shape[0] < 2:
            return self._zero_emb()

        # Model layouts:
        #   time-major:  [B, T, 80]
        #   freq-major:  [B, 80, T]
        if self._feats_time_major:
            x = feats[np.newaxis, :, :].astype(np.float32)  # [1, T, 80]
        else:
            x = feats.T[np.newaxis, :, :].astype(np.float32)  # [1, 80, T]

        try:
            out = self._session.run([self._output_name], {self._input_name: x})[0]
        except Exception:
            # retry alternate layout once
            try:
                if self._feats_time_major:
                    x_alt = feats.T[np.newaxis, :, :].astype(np.float32)
                else:
                    x_alt = feats[np.newaxis, :, :].astype(np.float32)
                out = self._session.run(
                    [self._output_name], {self._input_name: x_alt}
                )[0]
                self._feats_time_major = not self._feats_time_major
                logger.info(
                    "CAM++ input layout flipped to time_major=%s",
                    self._feats_time_major,
                )
            except Exception:
                logger.warning("CAM++ ORT embed failed", exc_info=True)
                return self._zero_emb()

        emb = np.asarray(out, dtype=np.float32).reshape(-1)
        n = float(np.linalg.norm(emb))
        if n < 1e-6:
            return self._zero_emb()
        return emb / n


def cluster_speakers(
    embeddings: Sequence[np.ndarray],
    *,
    threshold: float = 0.55,
) -> list[int]:
    """Agglomerative clustering by cosine similarity.

    Returns speaker id (0-based) per embedding. Empty input → [].
    ``threshold``: merge if cosine similarity >= threshold (lower → fewer speakers).

    All-zero / failed embeddings (invalid CAM++ input) are forced into speaker 0
    so a broken embed path cannot create one speaker per VAD segment.
    """
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]

    mats = np.stack([np.asarray(e, dtype=np.float32).reshape(-1) for e in embeddings], axis=0)
    norms = np.linalg.norm(mats, axis=1)
    valid = norms > 1e-5
    if not np.any(valid):
        # every embed failed → single speaker (avoid N unique labels)
        return [0] * n

    # Invalid rows: treat as speaker 0 after clustering valids (map later)
    norms_safe = np.where(valid, norms, 1.0)[:, None]
    X = mats / (norms_safe + 1e-8)
    sim = X @ X.T

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    valid_idx = [i for i in range(n) if valid[i]]
    for a_i, i in enumerate(valid_idx):
        for j in valid_idx[a_i + 1 :]:
            if sim[i, j] >= threshold:
                union(i, j)

    # Attach invalid embeddings to the largest valid cluster (or 0)
    if valid_idx:
        from collections import Counter

        root_counts = Counter(find(i) for i in valid_idx)
        dominant = root_counts.most_common(1)[0][0]
        for i in range(n):
            if not valid[i]:
                parent[i] = dominant

    roots = [find(i) for i in range(n)]
    mapping: dict[int, int] = {}
    labels: list[int] = []
    for r in roots:
        if r not in mapping:
            mapping[r] = len(mapping)
        labels.append(mapping[r])
    return labels


_BUNDLED_DIR = Path(__file__).resolve().parent / "campplus_models"
_CAMPPLUS_NAMES = (
    "campplus_zh_cn_common_200k.onnx",
    "campplus.onnx",
    "embedding.onnx",
    "model.onnx",
    "model_quant.onnx",
)


def find_campplus_onnx(model_dir: Path | None) -> Path | None:
    """Return the first loadable-looking CAM++ ONNX under ``model_dir``."""
    if model_dir is None or not model_dir.is_dir():
        return None
    seen: set[Path] = set()
    ordered: list[Path] = []
    for name in _CAMPPLUS_NAMES:
        ordered.append(model_dir / name)
    ordered.extend(sorted(model_dir.glob("*.onnx")))
    for p in ordered:
        if p in seen:
            continue
        seen.add(p)
        try:
            if p.is_file() and p.stat().st_size > 100_000:
                return p
        except OSError:
            continue
    return None


def bundled_campplus_dir() -> Path | None:
    """ONNX shipped next to this module (same pattern as RapidOCR)."""
    if find_campplus_onnx(_BUNDLED_DIR) is not None:
        return _BUNDLED_DIR
    return None


def resolve_campplus_dir() -> Path | None:
    """Bundled weights first, then downloaded ``data/models/onnx`` / HF snapshot."""
    bundled = bundled_campplus_dir()
    if bundled is not None:
        return bundled
    from src.meeting.transcription.onnx.paths import onnx_cache_dir, resolve_hf_snapshot

    cache = onnx_cache_dir("funasr/campplus")
    if find_campplus_onnx(cache) is not None:
        return cache
    snap = resolve_hf_snapshot("funasr/campplus")
    if find_campplus_onnx(snap) is not None:
        return snap
    return None


def try_load_campplus(
    model_dir: Path | None,
    *,
    num_threads: int = 4,
) -> CampplusOnnxEmbedder | None:
    """Load CAM++ onnx from a model dir if present."""
    p = find_campplus_onnx(model_dir)
    if p is None:
        return None
    try:
        return CampplusOnnxEmbedder(p, num_threads=num_threads)
    except Exception:
        logger.warning("Failed to load CAM++ ONNX %s", p, exc_info=True)
        return None
