from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Body

from src.config import get_config, DATA_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

VISUAL_SYSTEM_PROMPT = (
    "Describe this image concisely in 2-4 sentences. Cover: what is shown, key elements, "
    "and any visible text. Be brief and objective. Match the language of visible text, "
    "or use English if none."
)
DESCRIPTION_PREFIX = "[Image Description]: "


def _resolve_image_path(image_url: str) -> Path:
    """Convert an API image URL to the local filesystem path.

    Image URLs look like: /api/notes/{collection}/{note_id}/images/{filename}
    Files are stored at: data/notes/{collection}/{note_id}/images/{filename}
    """
    # Strip /api/ prefix
    relative = image_url.lstrip("/")
    if relative.startswith("api/"):
        relative = relative[4:]  # remove "api/"
    return DATA_DIR / relative


def _get_mime_type(path: Path) -> str:
    """Guess MIME type from file extension."""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


@router.post("/visual/describe")
def describe_image(body: dict = Body(...)):
    """Generate a text description of an image using the configured Visual Model.

    Request:
        { "image_url": "/api/notes/col/note_id/images/uuid.png" }

    Response 200:
        { "description": "[Image Description]: ..." }
    """
    image_url = body.get("image_url", "")
    if not image_url:
        return {"error": "image_url is required"}

    config = get_config()
    visual_model_id = config.visual_model_id
    if not visual_model_id:
        return {"error": "No Visual Model configured. Please select one in Settings."}

    # Find the provider that owns this visual model
    target_provider = None
    for p in config.llm.providers:
        if visual_model_id in p.visual_model_ids:
            target_provider = p
            break

    if not target_provider:
        return {
            "error": (
                f"Visual model '{visual_model_id}' not found in any provider. "
                "Please check your Visual Model settings."
            )
        }

    # Resolve image file
    try:
        image_path = _resolve_image_path(image_url)
        if not image_path.exists():
            return {"error": f"Image file not found: {image_path}"}
    except Exception as e:
        return {"error": f"Failed to resolve image path: {e}"}

    # Read and encode image
    try:
        image_data = image_path.read_bytes()
        image_base64 = base64.b64encode(image_data).decode("utf-8")
        image_mime = _get_mime_type(image_path)
    except Exception as e:
        logger.error("Failed to read image %s: %s", image_path, e)
        return {"error": f"Failed to read image file: {e}"}

    # Call vision model
    try:
        from src.providers.llm import create_llm_for_provider

        llm = create_llm_for_provider(target_provider, model=visual_model_id)
        raw_description = llm.describe_image(image_base64, image_mime)
        description = f"{DESCRIPTION_PREFIX}{raw_description}"
        logger.info(
            "Visual describe: model=%s image=%s desc_len=%d",
            visual_model_id,
            image_path.name,
            len(description),
        )
        return {"description": description}
    except NotImplementedError:
        return {
            "error": (
                f"The selected model '{visual_model_id}' does not support image input. "
                "Please choose a vision-capable model."
            )
        }
    except Exception as e:
        logger.error("Visual describe failed: %s", e)
        return {"error": f"Failed to generate image description: {e}"}
