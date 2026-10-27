#!/usr/bin/env bash
# Package local ONNX ASR packs for a GitHub Release asset.
#
# Layout (local only — never git commit; large binaries):
#   model_release/v<ver>/
#     sinkduce-onnx-models-v<ver>.zip
#     sinkduce-onnx-models-v<ver>.zip.sha256
#     MANIFEST-v<ver>.json
#     RELEASE-NOTES-v<ver>.md
#
# Usage (from repo root):
#   ./scripts/package_onnx_release.sh              # version = date YYYYMMDD
#   ./scripts/package_onnx_release.sh 1.0.0        # version = 1.0.0
#   ONNX_SRC=data/models/onnx ./scripts/package_onnx_release.sh 1.0.0
#
# Publish (independent of app tag vX.Y.Z):
#   gh release create onnx-models-v1.0.0 \
#     model_release/v1.0.0/sinkduce-onnx-models-v1.0.0.zip \
#     model_release/v1.0.0/sinkduce-onnx-models-v1.0.0.zip.sha256 \
#     model_release/v1.0.0/MANIFEST-v1.0.0.json \
#     --title "ONNX ASR models v1.0.0" \
#     --notes-file model_release/v1.0.0/RELEASE-NOTES-v1.0.0.md
#
# End users: app UI "Download local models" fetches this Release asset automatically.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="${1:-$(date +%Y%m%d)}"
ONNX_SRC="${ONNX_SRC:-data/models/onnx}"
# Per-version folder under model_release/ (keeps multiple zips side by side)
OUT_DIR="${OUT_DIR:-model_release/v${VERSION}}"
STAGE="${OUT_DIR}/.stage-onnx-${VERSION}"
ZIP_NAME="sinkduce-onnx-models-v${VERSION}.zip"
ZIP_PATH="${OUT_DIR}/${ZIP_NAME}"

# Packs required by SinkDuce local ONNX ASR (must match download.py repo_ids).
REQUIRED_DIRS=(
  "FunAudioLLM--SenseVoiceSmall"
  "funasr--fsmn-vad"
  "funasr--ct-punc"
  "funasr--campplus"
  "funasr--paraformer-zh-streaming"
)

# Files that must exist (glob relative to each pack dir). At least one match.
require_any() {
  local dir="$1"
  shift
  local f
  for f in "$@"; do
    if compgen -G "${dir}/${f}" > /dev/null; then
      return 0
    fi
  done
  return 1
}

echo "==> Source: ${ONNX_SRC}"
if [[ ! -d "${ONNX_SRC}" ]]; then
  echo "ERROR: ONNX source not found: ${ONNX_SRC}" >&2
  exit 1
fi

echo "==> Validating packs..."
missing=0
for d in "${REQUIRED_DIRS[@]}"; do
  if [[ ! -d "${ONNX_SRC}/${d}" ]]; then
    echo "  MISSING dir: ${d}"
    missing=1
    continue
  fi
  case "${d}" in
    FunAudioLLM--SenseVoiceSmall|funasr--ct-punc)
      if ! require_any "${ONNX_SRC}/${d}" "model_quant.onnx" "model.onnx"; then
        echo "  MISSING onnx in ${d}"
        missing=1
      fi
      ;;
    funasr--fsmn-vad)
      if ! require_any "${ONNX_SRC}/${d}" "model_quant.onnx" "model.onnx"; then
        echo "  MISSING onnx in ${d}"
        missing=1
      fi
      ;;
    funasr--campplus)
      if ! require_any "${ONNX_SRC}/${d}" "*.onnx"; then
        echo "  MISSING onnx in ${d}"
        missing=1
      fi
      ;;
    funasr--paraformer-zh-streaming)
      if ! require_any "${ONNX_SRC}/${d}" "model_quant.onnx" "model.onnx"; then
        echo "  MISSING encoder onnx in ${d}"
        missing=1
      fi
      if ! require_any "${ONNX_SRC}/${d}" "decoder_quant.onnx" "decoder.onnx"; then
        echo "  MISSING decoder onnx in ${d}"
        missing=1
      fi
      ;;
  esac
  echo "  OK ${d}"
done
if [[ "${missing}" -ne 0 ]]; then
  echo "ERROR: validation failed" >&2
  exit 1
fi

APACHE_LICENSE="third_party_licenses/Apache-2.0.txt"
FUNASR_MODEL_LICENSE="third_party_licenses/FunASR-MODEL_LICENSE.txt"
ATTRIBUTION_SOURCE="third_party_licenses/ONNX_ATTRIBUTION.md"
for license_file in "${APACHE_LICENSE}" "${FUNASR_MODEL_LICENSE}" "${ATTRIBUTION_SOURCE}"; do
  if [[ ! -s "${license_file}" ]]; then
    echo "ERROR: required model license missing: ${license_file}" >&2
    exit 1
  fi
done

echo "==> Staging slim pack (drop .cache, examples, redundant fp32 when quant exists)..."
rm -rf "${STAGE}"
mkdir -p "${STAGE}/onnx"

for d in "${REQUIRED_DIRS[@]}"; do
  src="${ONNX_SRC}/${d}"
  dst="${STAGE}/onnx/${d}"
  mkdir -p "${dst}"
  # Copy tree but skip junk
  rsync -a \
    --exclude='.cache' \
    --exclude='.gitattributes' \
    --exclude='__pycache__' \
    --exclude='example' \
    --exclude='fig' \
    --exclude='asr_example.wav' \
    --exclude='*.tmp' \
    "${src}/" "${dst}/"

  # Prefer int8 only for size: if model_quant.onnx exists, drop sibling model.onnx (fp32)
  if [[ -f "${dst}/model_quant.onnx" && -f "${dst}/model.onnx" ]]; then
    rm -f "${dst}/model.onnx"
    echo "  stripped fp32 model.onnx from ${d}"
  fi
  if [[ -f "${dst}/decoder_quant.onnx" && -f "${dst}/decoder.onnx" ]]; then
    rm -f "${dst}/decoder.onnx"
    echo "  stripped fp32 decoder.onnx from ${d}"
  fi
done

mkdir -p "${STAGE}/LICENSES"
cp "${APACHE_LICENSE}" "${STAGE}/LICENSES/Apache-2.0.txt"
cp "${FUNASR_MODEL_LICENSE}" "${STAGE}/LICENSES/FunASR-MODEL_LICENSE.txt"

# Manifest for humans + future download.py
MANIFEST="${OUT_DIR}/MANIFEST-v${VERSION}.json"
mkdir -p "${OUT_DIR}"
# Avoid bash ${var@Q} (needs bash 4.4+; macOS /bin/bash is 3.2)
export _SINK_ONNX_STAGE="${STAGE}"
export _SINK_ONNX_VERSION="${VERSION}"
export _SINK_ONNX_MANIFEST="${MANIFEST}"
"${PYTHON_BIN}" <<'PY'
import json, hashlib, os
from pathlib import Path

stage = Path(os.environ["_SINK_ONNX_STAGE"]) / "onnx"
version = os.environ["_SINK_ONNX_VERSION"]
manifest = Path(os.environ["_SINK_ONNX_MANIFEST"])
packs = {}
total = 0
for p in sorted(stage.iterdir()):
    if not p.is_dir():
        continue
    files = {}
    for f in sorted(p.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(p))
            h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            sz = f.stat().st_size
            total += sz
            files[rel] = {"bytes": sz, "sha256_16": h}
    packs[p.name] = files

doc = {
    "name": "sinkduce-onnx-models",
    "version": version,
    "layout": "unzip into data/models/ → data/models/onnx/<pack>/",
    "hf_home_onnx": "HF_HOME/onnx (default data/models/onnx)",
    "packs": packs,
    "total_bytes": total,
}
manifest.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
print("  packs=%d total_mb=%.1f" % (len(packs), total / 1e6))
PY

# Attribution + install notes (also copied into the zip root as ATTRIBUTION.md)
ATTRIBUTION_BODY="$(cat "${ATTRIBUTION_SOURCE}")"

NOTES="${OUT_DIR}/RELEASE-NOTES-v${VERSION}.md"
cat > "${NOTES}" <<EOF
# SinkDuce ONNX ASR models v${VERSION}

Pre-exported ONNX packs for **local** meeting transcription (no PyTorch at runtime).

## How this release is used

In the SinkDuce app UI, use **Download local models** (first-run dialog or **Settings → Local Models**).  
Clicking download **automatically fetches this Release asset** and installs packs under \`data/models/onnx/\`.

There is **no HuggingFace fallback**. How to install SinkDuce itself is documented separately (and may change).

## Contents

| Pack | Role |
|------|------|
| FunAudioLLM--SenseVoiceSmall | File ASR (multilingual-oriented int8) |
| funasr--fsmn-vad | VAD |
| funasr--ct-punc | Punctuation |
| funasr--campplus | Speaker embedding |
| funasr--paraformer-zh-streaming | Realtime ASR (Chinese-oriented streaming) |

${ATTRIBUTION_BODY}

## Notes

- Weights are **not** baked into the Docker image; they live under the \`data/\` volume.
- Runtime image only needs \`[asr-onnx]\` (onnxruntime + funasr-onnx).
- File transcription language coverage follows **SenseVoice**; realtime follows **Paraformer-zh-streaming** (not the same multilingual profile).
- Model package version is independent of the app version tag (\`vX.Y.Z\`).
EOF

# Ship attribution inside the zip (next to onnx/)
cp "${ATTRIBUTION_SOURCE}" "${STAGE}/ATTRIBUTION.md"

echo "==> Zipping ${ZIP_PATH} ..."
rm -f "${ZIP_PATH}"
# zip from stage so archive root has onnx/, ATTRIBUTION.md, and LICENSES/
( cd "${STAGE}" && zip -r -q "${ROOT}/${ZIP_PATH}" onnx ATTRIBUTION.md LICENSES )
# checksum
( cd "${OUT_DIR}" && shasum -a 256 "${ZIP_NAME}" > "${ZIP_NAME}.sha256" )

echo "==> Cleaning stage"
rm -rf "${STAGE}"

echo
echo "Done."
echo "  ${ZIP_PATH}"
echo "  ${ZIP_PATH}.sha256"
echo "  ${MANIFEST}"
echo "  ${NOTES}"
echo
echo "Publish with GitHub CLI (tag name onnx-models-v${VERSION}):"
echo "  gh release create onnx-models-v${VERSION} \\"
echo "    ${ZIP_PATH} \\"
echo "    ${ZIP_PATH}.sha256 \\"
echo "    ${MANIFEST} \\"
echo "    --title \"ONNX ASR models v${VERSION}\" \\"
echo "    --notes-file ${NOTES}"
echo
echo "R2 (public download channel used by download.py before the GitHub fallback):"
echo "  python3 desktop/r2_upload_models.py ${ZIP_PATH}"

# Optional auto-push to R2 (needs desktop/r2.env with an R2 API token's S3 keys).
# Run with an interpreter that has boto3; ONNX_R2_UPLOAD=1 opts in.
if [[ "${ONNX_R2_UPLOAD:-0}" == "1" ]]; then
  if [[ -f desktop/r2.env ]]; then
    echo "==> Uploading to R2 (ONNX_R2_UPLOAD=1)..."
    "${PYTHON_BIN}" desktop/r2_upload_models.py "${ZIP_PATH}"
  else
    echo "==> ONNX_R2_UPLOAD=1 but desktop/r2.env is missing; skip R2 upload" >&2
  fi
fi

echo
echo "Or upload the zip manually on GitHub → Releases → Draft."
