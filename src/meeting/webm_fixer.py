"""WebM duration fix using ffmpeg remux.

MediaRecorder produces WebM files with Cues at the end.
Browsers can't show duration until the full file is downloaded.
ffmpeg remux with ``-c copy`` rewrites the container so metadata
is at the start, without re-encoding.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.runtime_bins import ffmpeg_bin

logger = logging.getLogger("meeting.webm_fixer")


def fix_webm_duration(audio_path: Path) -> None:
    """Remux a WebM file with ffmpeg so browsers see duration metadata."""
    if audio_path.suffix.lower() not in (".webm",):
        return

    try:
        tmp_path = audio_path.with_suffix(".tmp.webm")
        if tmp_path.exists():
            tmp_path.unlink()

        result = subprocess.run(
            [
                ffmpeg_bin(), "-y",
                "-i", str(audio_path),
                "-c", "copy",
                "-map", "0",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg remux failed: %s", result.stderr[-200:])
            tmp_path.unlink(missing_ok=True)
            return

        if tmp_path.stat().st_size > 0:
            tmp_path.replace(audio_path)
            logger.info("Remuxed webm for duration: %s", audio_path)
        else:
            tmp_path.unlink()
    except FileNotFoundError:
        logger.warning("ffmpeg not available, cannot fix webm duration")
    except Exception as exc:
        logger.warning("Failed to remux webm: %s", exc)
