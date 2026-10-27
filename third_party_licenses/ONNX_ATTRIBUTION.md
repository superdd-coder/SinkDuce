# SinkDuce ONNX model attribution

This archive redistributes pre-exported ONNX weights for offline use with SinkDuce.
SinkDuce did not train these models. Copyright and licenses remain with the original
authors and projects. Full license texts are in `LICENSES/`; retain those files,
this attribution, and the model names when redistributing the archive.

| Pack (folder under `onnx/`) | Source and authors | License in `LICENSES/` |
|----------------------------|--------------------|-------------------------|
| FunAudioLLM--SenseVoiceSmall | [FunAudioLLM/SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall), Alibaba Group / FunAudioLLM | FunASR-MODEL_LICENSE.txt |
| funasr--fsmn-vad | [funasr/fsmn-vad](https://huggingface.co/funasr/fsmn-vad), Alibaba DAMO Academy / FunASR | Apache-2.0.txt |
| funasr--ct-punc | [manyeyes/punc_ct-transformer_zh-cn-common-vocab272727-onnx](https://huggingface.co/manyeyes/punc_ct-transformer_zh-cn-common-vocab272727-onnx), ONNX conversion by manyeyes of [Alibaba DAMO CT-Transformer](https://modelscope.cn/models/damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch) | Apache-2.0.txt ([conversion project](https://github.com/manyeyes/AliCTTransformerPunc)) |
| funasr--campplus | [damo/speech_campplus_sv_zh-cn_16k-common](https://modelscope.cn/models/damo/speech_campplus_sv_zh-cn_16k-common), Alibaba DAMO Academy | Apache-2.0.txt |
| funasr--paraformer-zh-streaming | [iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online](https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online), Alibaba DAMO Academy / FunASR | Apache-2.0.txt |

Packaging and ONNX runtime integration: SinkDuce project
([superdd-coder/SinkDuce](https://github.com/superdd-coder/SinkDuce)).
The SenseVoice model has its own license with different terms from Apache-2.0.
