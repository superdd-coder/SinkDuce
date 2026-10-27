---
license: apache-2.0
language:
- zh
tags:
- speaker-diarization
- speaker-embedding
- onnx
- campplus
---

# CAMPPlus zh-cn-common-200k (ONNX)

Speaker embedding model for diarization, exported from
[ModelScope damo/speech_campplus_sv_zh-cn_16k-common](https://modelscope.cn/models/damo/speech_campplus_sv_zh-cn_16k-common).

## Specs
- Architecture: CAM++ (Context-Aware Masking)
- Training data: CN-Celeb + CN Common (~200k speakers)
- Embedding dim: **192**
- Input: `feats` — `[batch, 80, time]` (80-dim FBANK, 16 kHz, 10ms hop)
- Output: `embs` — `[batch, 192]`

## Compatible with
[pyannote-rs](https://github.com/thewh1teagle/pyannote-rs) `EmbeddingExtractor`
(same `feats`/`embs` tensor names as `wespeaker_en_voxceleb_CAM++.onnx`).
