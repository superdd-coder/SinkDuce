"""File-level orchestration: VAD → ASR → punc → optional CAM++ (FunASR-style)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from src.meeting.models import TranscriptSegment, TranscriptionResult
from src.meeting.transcription.onnx.campplus import (
    cluster_speakers,
    try_load_campplus,
)
from src.meeting.transcription.onnx.paths import ensure_onnx_model_dir

logger = logging.getLogger(__name__)

_SENSEVOICE_TAG_RE = re.compile(r"<\|[^|]*\|>")


def _clean_text(text: str) -> str:
    return _SENSEVOICE_TAG_RE.sub("", text or "").strip()


def normalize_vad_raw(raw: Any) -> list[tuple[int, int]]:
    """Parse FSMN-VAD output into (start_ms, end_ms). Tolerate None holes."""
    segs: list[tuple[int, int]] = []
    if not raw:
        return segs
    level = raw
    if isinstance(level, list) and level:
        first = level[0]
        if isinstance(first, list) and first:
            head = first[0]
            if isinstance(head, (list, tuple)):
                level = first
    if not isinstance(level, (list, tuple)):
        return segs
    for item in level:
        if not item:
            continue
        if isinstance(item, (list, tuple)) and item and isinstance(item[0], (list, tuple)):
            for pair in item:
                if not pair or not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                if pair[0] is None or pair[1] is None:
                    continue
                segs.append((int(pair[0]), int(pair[1])))
            continue
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        if item[0] is None or item[1] is None:
            continue
        segs.append((int(item[0]), int(item[1])))
    return segs


def _normalize_vad_config_dir(vad_dir: Path) -> None:
    """Ensure vad_dir/config.yaml has model_conf for funasr_onnx.Fsmn_vad.

    HF ``funasr/fsmn-vad-onnx`` provides ``vad.yaml`` with ``vad_post_conf``;
    funasr-onnx expects ``config.yaml`` with key ``model_conf``.
    """
    import yaml

    cfg_path = vad_dir / "config.yaml"
    vad_yaml = vad_dir / "vad.yaml"
    data: dict[str, Any] | None = None

    if cfg_path.is_file():
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        if isinstance(data, dict) and "model_conf" in data and "frontend_conf" in data:
            return

    if (not data or "model_conf" not in data) and vad_yaml.is_file():
        try:
            data = yaml.safe_load(vad_yaml.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    # Map legacy key names
    if "model_conf" not in data and "vad_post_conf" in data:
        data["model_conf"] = data["vad_post_conf"]
    if "frontend_conf" not in data:
        data["frontend_conf"] = {
            "fs": 16000,
            "window": "hamming",
            "n_mels": 80,
            "frame_length": 25,
            "frame_shift": 10,
            "dither": 0.0,
            "lfr_m": 5,
            "lfr_n": 1,
        }
    if "encoder_conf" not in data:
        data["encoder_conf"] = {
            "input_dim": 400,
            "input_affine_dim": 140,
            "fsmn_layers": 4,
            "linear_dim": 250,
            "proj_dim": 128,
            "lorder": 20,
            "rorder": 0,
            "lstride": 1,
            "rstride": 0,
            "output_affine_dim": 140,
            "output_dim": 248,
        }
    if "model_conf" not in data:
        # Minimal defaults matching funasr/fsmn-vad
        data["model_conf"] = {
            "sample_rate": 16000,
            "detect_mode": 1,
            "snr_mode": 0,
            "max_end_silence_time": 800,
            "max_start_silence_time": 3000,
            "do_start_point_detection": True,
            "do_end_point_detection": True,
            "window_size_ms": 200,
            "sil_to_speech_time_thres": 150,
            "speech_to_sil_time_thres": 150,
            "speech_2_noise_ratio": 1.0,
            "do_extend": 1,
            "lookback_time_start_point": 200,
            "lookahead_time_end_point": 100,
            "max_single_segment_time": 60000,
            "snr_thres": -100.0,
            "noise_frame_num_used_for_snr": 100,
            "decibel_thres": -100.0,
            "speech_noise_thres": 0.6,
            "fe_prior_thres": 0.0001,
            "silence_pdf_num": 1,
            "sil_pdf_ids": [0],
            "speech_noise_thresh_low": -0.1,
            "speech_noise_thresh_high": 0.3,
            "output_frame_probs": False,
            "frame_in_ms": 10,
            "frame_length_ms": 25,
        }

    # cmvn file name: official pack uses vad.mvn; funasr_onnx looks for am.mvn
    if not (vad_dir / "am.mvn").is_file() and (vad_dir / "vad.mvn").is_file():
        import shutil

        shutil.copy2(vad_dir / "vad.mvn", vad_dir / "am.mvn")

    cfg_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    logger.info("Normalized VAD config.yaml (model_conf) in %s", vad_dir)


def _resample_mono_linear(audio: np.ndarray, sr: int, target_sr: int = 16000) -> np.ndarray:
    """Lightweight linear resample (no torch / scipy required)."""
    if sr == target_sr:
        return np.asarray(audio, dtype=np.float32)
    if audio.size == 0 or sr <= 0:
        return np.zeros(0, dtype=np.float32)
    duration = len(audio) / float(sr)
    n = int(duration * target_sr)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _load_via_ffmpeg(path: str, target_sr: int = 16000) -> np.ndarray:
    """Decode any ffmpeg-supported container (WebM/Opus, MP3, M4A, …) to mono float32."""
    import subprocess

    from src.runtime_bins import ffmpeg_bin

    ff = ffmpeg_bin()
    if ff == "ffmpeg":
        import shutil

        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "ffmpeg is required to decode non-WAV audio "
                f"(got {Path(path).suffix or 'unknown format'}): {path}"
            )
    cmd = [
        ff,
        "-v",
        "error",
        "-i",
        path,
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(target_sr),
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg failed to decode {path}: {err}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if audio.size == 0:
        logger.warning("ffmpeg produced empty audio for %s", path)
    return audio


def _load_wav_mono16k(path: str) -> tuple[np.ndarray, int]:
    """Load audio as mono float32 @ 16 kHz.

    Browser meeting recordings are typically ``.webm`` (Opus). ``soundfile``/
    libsndfile cannot open WebM, so we fall back to ffmpeg (already used for
    duration remux in ``webm_fixer``).
    """
    path_obj = Path(path)
    suffix = path_obj.suffix.lower()
    # Formats soundfile/libsndfile usually handle without ffmpeg.
    soundfile_ok = suffix in {".wav", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".au"}

    if soundfile_ok:
        try:
            import soundfile as sf

            audio, sr = sf.read(path, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return _resample_mono_linear(np.asarray(audio, dtype=np.float32), int(sr)), 16000
        except Exception:
            logger.info("soundfile failed for %s, trying ffmpeg", path, exc_info=True)

    # WebM / MP3 / M4A / unknown / soundfile failure
    try:
        return _load_via_ffmpeg(path, target_sr=16000), 16000
    except Exception as ffmpeg_exc:
        # Last resort: soundfile anyway (might work for misnamed files)
        try:
            import soundfile as sf

            audio, sr = sf.read(path, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return _resample_mono_linear(np.asarray(audio, dtype=np.float32), int(sr)), 16000
        except Exception as sf_exc:
            raise RuntimeError(
                f"Cannot decode audio {path}: soundfile={sf_exc}; ffmpeg={ffmpeg_exc}"
            ) from ffmpeg_exc


class FunAsrOnnxFilePipeline:
    """Mirrors AutoModel(vad + asr + punc + optional spk) using ORT only at runtime."""

    def __init__(
        self,
        *,
        asr_repo: str = "FunAudioLLM/SenseVoiceSmall",
        vad_repo: str = "funasr/fsmn-vad",
        punc_repo: str | None = "funasr/ct-punc",
        spk_repo: str | None = "funasr/campplus",
        quantize: bool = True,
        num_threads: int = 4,
        device_id: str = "-1",
        # SenseVoice always loads int8 model_quant.onnx (user requirement).
        asr_quantize: bool | None = None,
    ):
        self._quantize = quantize
        # ASR (SenseVoice): force int8 unless explicitly overridden
        self._asr_quantize = True if asr_quantize is None else asr_quantize
        self._num_threads = num_threads
        self._device_id = device_id

        asr_dir = ensure_onnx_model_dir(
            asr_repo,
            streaming=False,
            quantize=self._asr_quantize,
            label="ASR-SenseVoice-int8",
        )
        vad_dir = ensure_onnx_model_dir(
            vad_repo, streaming=False, quantize=quantize, label="VAD"
        )

        from funasr_onnx import Fsmn_vad, SenseVoiceSmall

        # Official funasr/fsmn-vad-onnx ships vad.yaml (vad_post_conf) without
        # model_conf; funasr_onnx.Fsmn_vad requires config.yaml["model_conf"].
        _normalize_vad_config_dir(vad_dir)

        self._vad = Fsmn_vad(
            str(vad_dir),
            quantize=quantize,
            intra_op_num_threads=num_threads,
            device_id=device_id,
        )
        self._asr = SenseVoiceSmall(
            str(asr_dir),
            quantize=self._asr_quantize,
            intra_op_num_threads=num_threads,
            device_id=device_id,
        )
        quant_file = asr_dir / (
            "model_quant.onnx" if self._asr_quantize else "model.onnx"
        )
        logger.info(
            "ONNX file pipeline ASR=%s quantize=%s file=%s exists=%s VAD=%s",
            asr_dir,
            self._asr_quantize,
            quant_file.name,
            quant_file.exists(),
            vad_dir,
        )

        self._punc = None
        if punc_repo:
            try:
                from funasr_onnx import CT_Transformer

                punc_dir = ensure_onnx_model_dir(
                    punc_repo, streaming=False, quantize=quantize, label="punc"
                )
                self._punc = CT_Transformer(
                    str(punc_dir),
                    quantize=quantize,
                    intra_op_num_threads=num_threads,
                    device_id=device_id,
                )
                logger.info("ONNX CT-punc ready: %s", punc_dir)
            except Exception:
                logger.warning("ONNX punctuation unavailable", exc_info=True)

        self.last_segment_embeddings: list[np.ndarray] | None = None
        self._spk = None
        if spk_repo:
            try:
                from src.meeting.transcription.onnx.campplus import resolve_campplus_dir

                spk_dir = resolve_campplus_dir()
                if spk_dir is None:
                    spk_dir = ensure_onnx_model_dir(
                        spk_repo, streaming=False, quantize=quantize, label="speaker"
                    )
                self._spk = try_load_campplus(spk_dir, num_threads=num_threads)
                if self._spk is None:
                    logger.warning(
                        "CAM++ ONNX not found under %s — diarization disabled",
                        spk_dir,
                    )
                else:
                    logger.info("CAM++ ready: %s", spk_dir)
            except Exception:
                logger.warning("CAM++ load failed — diarization disabled", exc_info=True)

    def _vad_segments_ms(self, waveform: np.ndarray) -> list[tuple[int, int]]:
        """Return list of (start_ms, end_ms) from FSMN-VAD."""
        return normalize_vad_raw(self._vad(waveform))

    def _asr_segment(self, wav: np.ndarray, language: str = "auto") -> str:
        res = self._asr(wav, language=language)
        # SenseVoice returns list of dicts or list of strings depending on version
        if not res:
            return ""
        first = res[0]
        if isinstance(first, dict):
            text = first.get("text") or first.get("preds") or ""
            if isinstance(text, (list, tuple)):
                text = "".join(str(t) for t in text)
            return _clean_text(str(text))
        return _clean_text(str(first))

    @staticmethod
    def _length_batches(
        wavs: list[np.ndarray],
        *,
        max_batch: int = 4,
        max_spread: float = 2.0,
        long_samples: int = 16_000 * 20,
    ) -> list[list[int]]:
        """Group similar-length clips so padded SenseVoice batches stay cheap."""
        order = sorted(range(len(wavs)), key=lambda i: wavs[i].size)
        groups: list[list[int]] = []
        cur: list[int] = []
        for i in order:
            n = int(wavs[i].size)
            if n >= long_samples:
                if cur:
                    groups.append(cur)
                    cur = []
                groups.append([i])
                continue
            if not cur:
                cur = [i]
                continue
            shortest = int(wavs[cur[0]].size) or 1
            if len(cur) >= max_batch or n > shortest * max_spread:
                groups.append(cur)
                cur = [i]
            else:
                cur.append(i)
        if cur:
            groups.append(cur)
        return groups

    def _decode_sensevoice_logits(self, ctc_logits: Any, encoder_out_lens: Any, b: int) -> str:
        x = ctc_logits[b, : int(encoder_out_lens[b]), :]
        yseq = np.argmax(x, axis=-1)
        mask = np.concatenate(([True], np.diff(yseq) != 0))
        yseq = yseq[mask]
        token_int = yseq[yseq != getattr(self._asr, "blank_id", 0)].tolist()
        return _clean_text(str(self._asr.tokenizer.decode(token_int)))

    def _asr_many(self, wavs: list[np.ndarray], language: str = "auto") -> list[str]:
        """Batch similar-length VAD clips through SenseVoice when possible."""
        if not wavs:
            return []
        if len(wavs) == 1:
            return [self._asr_segment(wavs[0], language=language)]

        lid = int(getattr(self._asr, "lid_dict", {}).get(language, 0))
        tnid = int(getattr(self._asr, "textnorm_dict", {}).get("woitn", 15))
        out = [""] * len(wavs)
        try:
            for group in self._length_batches(wavs):
                if len(group) == 1:
                    out[group[0]] = self._asr_segment(wavs[group[0]], language=language)
                    continue
                batch = [wavs[i] for i in group]
                feats, feats_len = self._asr.extract_feat(batch)
                bsz = int(feats.shape[0])
                langs = np.full((bsz,), lid, dtype=np.int32)
                tns = np.full((bsz,), tnid, dtype=np.int32)
                ctc_logits, encoder_out_lens = self._asr.infer(
                    feats, feats_len, langs, tns
                )
                for j, idx in enumerate(group):
                    out[idx] = self._decode_sensevoice_logits(
                        ctc_logits, encoder_out_lens, j
                    )
        except Exception:
            logger.warning("batched SenseVoice failed, falling back per segment", exc_info=True)
            return [self._asr_segment(w, language=language) for w in wavs]
        return out

    def _punctuate(self, text: str) -> str:
        if not text or self._punc is None:
            return text
        try:
            out = self._punc(text)
            if isinstance(out, (list, tuple)) and out:
                return str(out[0])
            return str(out)
        except Exception:
            logger.warning("punc failed for %r", text[:40], exc_info=True)
            return text

    def transcribe(
        self,
        file_path: str,
        *,
        language: str = "auto",
    ) -> TranscriptionResult:
        waveform, sr = _load_wav_mono16k(file_path)
        if waveform.size == 0:
            return TranscriptionResult(text="", segments=[])

        segs_ms = self._vad_segments_ms(waveform)
        if not segs_ms:
            # whole file as one utterance
            segs_ms = [(0, int(len(waveform) / sr * 1000))]

        texts: list[str] = []
        times: list[tuple[float, float]] = []
        emb_list: list[np.ndarray] = []
        chunks: list[tuple[float, float, np.ndarray]] = []

        for s_ms, e_ms in segs_ms:
            s = max(0, int(s_ms * sr / 1000))
            e = min(len(waveform), int(e_ms * sr / 1000))
            if e - s < sr // 20:
                continue
            chunks.append((s_ms / 1000.0, e_ms / 1000.0, waveform[s:e]))

        raw_texts = self._asr_many([c[2] for c in chunks], language=language)
        for (t0, t1, chunk), text in zip(chunks, raw_texts):
            if not text:
                continue
            text = self._punctuate(text)
            texts.append(text)
            times.append((t0, t1))
            if self._spk is not None:
                try:
                    emb_list.append(self._spk.embed(chunk, sample_rate=sr))
                except Exception:
                    emb_list.append(np.zeros(192, dtype=np.float32))

        if self._spk is not None and emb_list and len(emb_list) == len(texts):
            # 0.55: same speaker across VAD gaps merges more readily than 0.7
            # (0.7 + failed/zero embeds previously made every line a new spk).
            labels = cluster_speakers(emb_list, threshold=0.55)
            speaker_ids: list[str | None] = [f"spk{i}" for i in labels]
        else:
            speaker_ids = [None] * len(texts)

        segments = [
            TranscriptSegment(
                start=t0,
                end=t1,
                text=tx,
                speaker_id=sid,
            )
            for (t0, t1), tx, sid in zip(times, texts, speaker_ids)
        ]
        full = "".join(s.text for s in segments) if any(
            self._is_cjk_text(s.text) for s in segments
        ) else " ".join(s.text for s in segments)
        self.last_segment_embeddings = emb_list if emb_list and len(emb_list) == len(segments) else None
        logger.info(
            "ONNX file pipeline done: %d segments, speakers=%s",
            len(segments),
            len({s.speaker_id for s in segments if s.speaker_id}),
        )
        return TranscriptionResult(text=full, segments=segments)

    @staticmethod
    def _is_cjk_text(text: str) -> bool:
        return any("\u4e00" <= c <= "\u9fff" for c in text)
