"""Opt-in real Writer verification; never mounts production runtime data."""
import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("SINKDUCE_REPORT_TEST_IMAGE"),
    reason="Set SINKDUCE_REPORT_TEST_IMAGE for isolated real Writer export checks")


@pytest.fixture(scope="module")
def rendered_report(tmp_path_factory):
    work = tmp_path_factory.mktemp("report-writer")
    script = r'''
import json
from pathlib import Path
import pdfplumber
from src.chatbox.citations import EvidenceRegistry
from src.reports.rendering import render_docx, render_html, render_pdf
from src.reports.assets import SourceImages
from src.file_mgmt import storage_paths
from PIL import Image, ImageDraw

out = Path("/verification")
r = EvidenceRegistry("verification")
e = r.register("基线等待时间为 6.0 天，试点为 4.2 天。统计口径一致。", {"file_id":"file-a", "source_label":"试点记录.pdf", "page_number":4})
image_dir = storage_paths.version_dir("col", "file-a", "v1") / "images"
image_dir.mkdir(parents=True)
source_image = Image.new("RGB", (900, 240), "#f1f3f1")
draw = ImageDraw.Draw(source_image)
draw.rectangle((30, 50, 650, 100), fill="#1a5e3d")
draw.rectangle((30, 140, 450, 190), fill="#0f4a2d")
source_image.save(image_dir / "source-chart.png")
# Entirely fictional inventory data; never copy uploaded business records.
headers = ["No.", "Item", "Category", "Units", "Weight (kg)", "Budget", "Batch", "Ordered", "Received", "Checked", "Status"]
row = ["1", "Crate-A", "Synthetic sample", "18", "42", "560", "LOT-A", "Q1-2034", "Q2-2034", "Q4-2034", "Awaiting quality inspection"]
wide = "|" + "|".join(headers) + "|\n|" + "|".join(["---"] * 11) + "|\n" + ("|" + "|".join(row) + "|\n") * 16
image_evidence = r.register(":::image\nimage_id: source-chart\nfile_id: file-a\n:::\n\n" + wide, {"collection":"col", "file_id":"file-a", "version_id":"v1", "source_label":"图表依据.pdf", "page_number":3})
resolver = SourceImages("verification", out / "attachments")
body = f"## 执行摘要\n\n交接等待时间减少约 30%。[cite:{e['id']}]\n\n"
body += "| 阶段 | 平均等待时间 |\n|---|---|\n|基线|6.0 天|\n|试点|4.2 天|\n\n"
body += '```svg\n<svg xmlns="http://www.w3.org/2000/svg" font-family="Helvetica, Arial, sans-serif" width="640" height="200"><rect x="50" y="40" width="400" height="35" fill="#a5b59c"/><rect x="50" y="110" width="280" height="35" fill="#245637"/><text x="470" y="65" font-size="22">6.0</text><text x="350" y="135" font-size="22">4.2</text></svg>\n```\n\n'
body += f"图 1：两阶段等待时间对比。[cite:{e['id']}]\n\n"
body += f"## 虚构库存明细\n\n数据依据[cite:{image_evidence['id']}]\n\n" + wide + "\n\n"
for n in range(1,3):
    body += f"## {n}. 后续行动与验证\n\n"
    body += ("建议在后续批次中继续核对执行标准、责任归属和等待时间，结合异常记录判断改进是否稳定。此项为报告建议。\n\n" * 5)
report = {"title":"试点复盘与改进建议", "markdown":body, "citations":[e, image_evidence], "revision":1}
(out/"report.html").write_bytes(render_html(report, source_images=resolver))
(out/"report.docx").write_bytes(render_docx(report, source_images=resolver))
pdf = render_pdf(report, source_images=resolver)
(out/"report.pdf").write_bytes(pdf)
with pdfplumber.open(out/"report.pdf") as document:
    for index, page in enumerate(document.pages, 1):
        page.to_image(resolution=110).original.save(out / f"page-{index}.png")
    text="\n".join(p.extract_text() or "" for p in document.pages)
    links=sum(len(p.annots or []) for p in document.pages)
    image=document.pages[0].to_image(resolution=110).original.convert("RGB")
    image.save(out/"report-page-1.png")
    document.pages[-1].to_image(resolution=110).original.save(out/"report-page-last.png")
    green=sum(1 for red,g,b in image.getdata() if g>red*1.15 and g>b*1.05 and g<180)
    blue=sum(1 for red,g,b in image.getdata() if b>red*1.15 and b>g*1.05 and b<240)
    import zipfile,io
    from PIL import Image
    with zipfile.ZipFile(out/"report.docx") as archive:
        chart=Image.open(io.BytesIO(archive.read("word/media/image1.png"))).convert("RGBA")
        labels=sum(1 for red,g,b,alpha in chart.crop((470,40,540,80)).getdata() if alpha>128 and max(red,g,b)<180)
    result={"pages":len(document.pages), "text":text, "links":links, "green_pixels":green, "blue_pixels":blue, "chart_label_pixels":labels, "pdf_bytes":len(pdf), "landscape_pages":sum(p.width > p.height for p in document.pages), "source_images":sum(len(p.images) for p in document.pages), "page_texts":[p.extract_text() or "" for p in document.pages]}
print(json.dumps(result,ensure_ascii=False))
'''
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint", "python",
        "--mount", f"type=bind,source={repo / 'src'},target=/app/src,readonly",
        "--mount", f"type=bind,source={work},target=/verification",
        "-e", "SINKDUCE_DATA=/tmp/sinkduce-report-test", os.environ["SINKDUCE_REPORT_TEST_IMAGE"], "-c", script],
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-5000:]
    payload = json.loads(result.stdout.splitlines()[-1])
    return work, payload


def test_real_writer_preserves_chinese_tables_and_source_excerpts(rendered_report):
    work, result = rendered_report
    assert result["pages"] >= 2
    assert "试点复盘与改进建议" in result["text"]
    assert "基线" in result["text"] and "4.2" in result["text"]
    assert "试点记录.pdf" in result["text"]
    assert "统计口径一致" in result["text"]
    assert "[cite:" not in result["text"]
    assert all((work / ("report." + ext)).stat().st_size > 100 for ext in ("html", "docx", "pdf"))


def test_real_writer_keeps_clickable_internal_citations_and_visible_chart(rendered_report):
    _, result = rendered_report
    assert result["links"] >= 2
    assert result["green_pixels"] > 500
    assert result["blue_pixels"] < 50, "Writer must keep the report palette rather than the stock blue Word table theme"
    assert result["chart_label_pixels"] > 25, "SVG chart labels must remain visible in the exported files"


def test_real_writer_fixture_reports_its_qa_images(rendered_report):
    work, result = rendered_report
    assert (work / "report-page-1.png").is_file()
    assert (work / "report-page-last.png").is_file()
    print(json.dumps({"artifact_directory": str(work), "pages": result["pages"], "pdf_bytes": result["pdf_bytes"]}))


def test_real_writer_renders_wide_tables_and_source_images_without_raw_blocks(rendered_report):
    work, result = rendered_report
    assert result["landscape_pages"] >= 2
    assert result["source_images"] >= 2
    assert ":::image" not in result["text"] and "image_id:" not in result["text"]
    assert "Crate-A" in result["text"] and "Q4-2034" in result["text"]
    assert "data:image/png;base64," in (work / "report.html").read_text()


def test_real_writer_has_no_empty_trailing_page_or_broken_short_headers(rendered_report):
    _, result = rendered_report
    assert all(len(text.strip()) > 80 for text in result["page_texts"])
    assert "Budget" in result["text"] and "Ordered" in result["text"] and "Batch" in result["text"]
