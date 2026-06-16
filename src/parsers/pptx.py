from pathlib import Path
from pptx import Presentation
from src.parsers.base import DocumentParser, ParsedDocument


class PptxParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        prs = Presentation(str(path))
        slides_text = []
        for i, slide in enumerate(prs.slides):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    texts.append(shape.text_frame.text)
            if texts:
                slides_text.append(f"## Slide {i + 1}\n" + "\n".join(texts))

        # Build content with position_map tracking slide boundaries
        position_map: list[dict] = []
        parts: list[str] = []
        offset = 0
        for i, slide_text in enumerate(slides_text):
            position_map.append({
                "char_offset": offset,
                "label": f"Slide {i + 1}",
                "type": "slide",
                "slide_number": i + 1,
            })
            parts.append(slide_text)
            offset += len(slide_text) + 2  # +2 for "\n\n" separator

        return ParsedDocument(
            content="\n\n".join(parts),
            metadata={"slides": len(slides_text)},
            source_path=str(path),
            file_type="pptx",
            position_map=position_map,
        )
