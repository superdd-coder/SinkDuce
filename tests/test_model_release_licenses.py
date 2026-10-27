"""The downloadable ONNX bundle must carry its redistribution terms."""

import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def test_onnx_release_contains_model_licenses_and_provenance(tmp_path):
    source = tmp_path / "models"
    required_models = {
        "FunAudioLLM--SenseVoiceSmall": ("model_quant.onnx",),
        "funasr--fsmn-vad": ("model_quant.onnx",),
        "funasr--ct-punc": ("model_quant.onnx",),
        "funasr--campplus": ("campplus.onnx",),
        "funasr--paraformer-zh-streaming": ("model_quant.onnx", "decoder_quant.onnx"),
    }
    for directory, names in required_models.items():
        model_dir = source / directory
        model_dir.mkdir(parents=True)
        for name in names:
            (model_dir / name).write_bytes(b"fixture model")

    output = tmp_path / "release"
    env = os.environ.copy()
    env.update(ONNX_SRC=str(source), OUT_DIR=os.path.relpath(output, ROOT), PYTHON_BIN=sys.executable)
    subprocess.run(
        ["bash", "scripts/package_onnx_release.sh", "0.0.0"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    archive = output / "sinkduce-onnx-models-v0.0.0.zip"
    with ZipFile(archive) as bundle:
        assert "LICENSES/Apache-2.0.txt" in bundle.namelist()
        assert "LICENSES/FunASR-MODEL_LICENSE.txt" in bundle.namelist()
        assert "Apache License" in bundle.read("LICENSES/Apache-2.0.txt").decode()
        assert "FunASR Model Open Source License Agreement" in bundle.read(
            "LICENSES/FunASR-MODEL_LICENSE.txt"
        ).decode()
        attribution = bundle.read("ATTRIBUTION.md").decode()
        assert "manyeyes/punc_ct-transformer_zh-cn-common-vocab272727-onnx" in attribution
        assert "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online" in attribution
