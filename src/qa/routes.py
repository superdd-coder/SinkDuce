"""Collection-scoped Q&A HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from src.api.routes.identity import require_module

from . import (
    corrections,
    deletion,
    draft_stream,
    drafting,
    drafts,
    excel,
    exports,
    imports,
    intelligence,
    materials,
    metadata,
    outline,
    service,
    store,
    templates,
)
from . import models as m

router = APIRouter(
    prefix="/collections/{collection_id}/qa", dependencies=[Depends(require_module("database"))]
)


@router.get("/lists")
def lists(collection_id: str):
    return service.list_lists(collection_id)


@router.get("/questions")
def project_questions(collection_id: str, include_omitted: bool = False):
    return service.project_questions(collection_id, include_omitted)


@router.post("/lists", status_code=201)
def create_list(collection_id: str, data: m.CreateList):
    return service.create_list(collection_id, data)


@router.get("/lists/{list_id}")
def detail(collection_id: str, list_id: str, version_id: str | None = None):
    return service.detail(collection_id, list_id, version_id)


@router.patch("/lists/{list_id}")
def update_list(collection_id: str, list_id: str, data: m.ListPatch):
    return service.update_list(collection_id, list_id, data)


@router.delete("/lists/{list_id}")
def delete_list(collection_id: str, list_id: str, data: m.ListDelete):
    return deletion.remove(collection_id, list_id, data)


@router.post("/lists/{list_id}/questions")
def add_question(collection_id: str, list_id: str, data: m.QuestionCreate):
    return service.add_question(collection_id, list_id, data)


@router.post("/lists/{list_id}/outline")
def create_outline(collection_id: str, list_id: str, data: m.OutlineCreate):
    return outline.create_item(collection_id, list_id, data)


@router.post("/lists/{list_id}/outline/{item_id}/move")
def move_outline(collection_id: str, list_id: str, item_id: str, data: m.OutlineMove):
    return outline.move_item(collection_id, list_id, item_id, data)


@router.post("/lists/{list_id}/outline/undo")
def undo_outline(collection_id: str, list_id: str, data: m.Mutation):
    return outline.undo(collection_id, list_id, data)


@router.patch("/lists/{list_id}/outline/{item_id}")
def edit_outline(collection_id: str, list_id: str, item_id: str, data: m.OutlinePatch):
    return outline.edit_item(collection_id, list_id, item_id, data)


@router.patch("/lists/{list_id}/questions/{question_id}")
def update_question(collection_id: str, list_id: str, question_id: str, data: m.QuestionPatch):
    return service.update_question(collection_id, list_id, question_id, data)


@router.post("/lists/{list_id}/questions/{question_id}/membership")
def membership(collection_id: str, list_id: str, question_id: str, data: m.Membership):
    return service.membership(collection_id, list_id, question_id, data)


@router.post("/lists/{list_id}/columns")
def add_columns(collection_id: str, list_id: str, data: m.ColumnsCreate):
    return service.add_columns(collection_id, list_id, data)


@router.patch("/lists/{list_id}/columns/{column_id}")
def update_column(collection_id: str, list_id: str, column_id: str, data: m.ColumnPatch):
    return service.update_column(collection_id, list_id, column_id, data)


@router.put("/lists/{list_id}/questions/{question_id}/replies/{column_id}")
def save_reply(
    collection_id: str, list_id: str, question_id: str, column_id: str, data: m.ReplyPut
):
    return service.save_reply(collection_id, list_id, question_id, column_id, data)


@router.get("/lists/{list_id}/questions/{question_id}/replies/{column_id}/history")
def reply_history(
    collection_id: str,
    list_id: str,
    question_id: str,
    column_id: str,
    version_id: str | None = None,
):
    return service.reply_history(collection_id, list_id, question_id, column_id, version_id)


@router.get("/lists/{list_id}/versions")
def versions(collection_id: str, list_id: str):
    return service.versions(collection_id, list_id)


@router.post("/lists/{list_id}/versions")
def next_version(collection_id: str, list_id: str, data: m.Mutation):
    return service.next_version(collection_id, list_id, data)


@router.post("/lists/{list_id}/imports")
def import_file(
    collection_id: str,
    list_id: str,
    file: Annotated[UploadFile, File()],
    expected_revision: Annotated[int, Form()],
):
    # Authorize before parsing or persisting the workbook.
    with store.connection(collection_id, "update") as conn:
        store.get_list(conn, list_id)
    return imports.prepare(
        collection_id,
        list_id,
        file.file.read(excel.MAX_UPLOAD + 1),
        file.filename or "",
        expected_revision,
    )


@router.get("/lists/{list_id}/imports/{import_id}")
def import_plan(collection_id: str, list_id: str, import_id: str):
    return imports.read(collection_id, list_id, import_id)


@router.patch("/lists/{list_id}/imports/{import_id}")
def update_import(collection_id: str, list_id: str, import_id: str, data: m.ImportPatch):
    return imports.update(collection_id, list_id, import_id, data.model_dump(exclude_none=True))


@router.post("/lists/{list_id}/imports/{import_id}/commit")
def commit_import(
    collection_id: str,
    list_id: str,
    import_id: str,
    data: m.ImportCommit,
    background: BackgroundTasks,
):
    result = imports.commit(collection_id, list_id, import_id, data.plan_revision)
    from .pipeline import summarize

    background.add_task(summarize, collection_id, list_id, result["revision"])
    return result


@router.post("/lists/{list_id}/imports/{import_id}/analyze")
def analyze_import(collection_id: str, list_id: str, import_id: str, data: m.ImportCommit):
    return intelligence.analyze(collection_id, list_id, import_id, data.plan_revision)


@router.get("/lists/{list_id}/questions/{question_id}/drafts/{column_id}")
def get_draft(collection_id: str, list_id: str, question_id: str, column_id: str, version_id: str):
    return drafts.read(collection_id, list_id, version_id, question_id, column_id)


@router.put("/lists/{list_id}/questions/{question_id}/drafts/{column_id}")
def put_draft(collection_id: str, list_id: str, question_id: str, column_id: str, data: m.DraftPut):
    return drafts.put(collection_id, list_id, question_id, column_id, data)


@router.delete("/lists/{list_id}/questions/{question_id}/drafts/{column_id}")
def delete_draft(
    collection_id: str,
    list_id: str,
    question_id: str,
    column_id: str,
    version_id: str,
    draft_revision: int,
):
    return drafts.discard(
        collection_id, list_id, version_id, question_id, column_id, draft_revision
    )


@router.get("/lists/{list_id}/versions/{version_id}/original")
def original(collection_id: str, list_id: str, version_id: str):
    with store.connection(collection_id) as conn:
        row = conn.execute(
            "SELECT id,plan_json FROM qa_imports WHERE list_id=? AND committed_version_id=?",
            (list_id, version_id),
        ).fetchone()
        if not row:
            store.fail("not_found", 404)
        return FileResponse(
            imports.asset_path(collection_id, list_id, "imports", row["id"]),
            filename="Q&A-original.xlsx",
        )


@router.post("/lists/{list_id}/exports")
def export_file(collection_id: str, list_id: str, data: m.ExportCreate):
    return exports.create(collection_id, list_id, data.model_dump(exclude_unset=True))


@router.get("/lists/{list_id}/exports")
def export_history(collection_id: str, list_id: str):
    return exports.list_exports(collection_id, list_id)


@router.post("/lists/{list_id}/questions/{question_id}/drafts/{column_id}/generate")
def generate_draft(
    collection_id: str, list_id: str, question_id: str, column_id: str, data: m.DraftGenerate
):
    return drafting.generate(collection_id, list_id, question_id, column_id, data)


@router.post("/lists/{list_id}/questions/{question_id}/drafts/{column_id}/generate/stream")
async def stream_draft(
    request: Request,
    collection_id: str,
    list_id: str,
    question_id: str,
    column_id: str,
    data: m.DraftGenerate,
):
    return draft_stream.response(request, collection_id, list_id, question_id, column_id, data)


@router.get("/templates")
def list_templates(collection_id: str):
    return templates.list_templates(collection_id)


@router.post("/templates", status_code=201)
def create_template(collection_id: str, data: m.TemplateInput):
    return templates.save(collection_id, data)


@router.patch("/templates/{template_id}")
def update_template(collection_id: str, template_id: str, data: m.TemplateInput):
    return templates.save(collection_id, data, template_id)


@router.post("/logos")
def upload_logo(collection_id: str, file: Annotated[UploadFile, File()]):
    with store.connection(collection_id, "update"):
        return templates.upload_logo(collection_id, file.file.read(3 * 1024 * 1024 + 1))


@router.get("/logos/{logo_id}")
def get_logo(collection_id: str, logo_id: str):
    with store.connection(collection_id):
        path = imports.asset_path(collection_id, "templates", "logos", logo_id)
        if not path.is_file():
            store.fail("not_found", 404)
        return FileResponse(path)


@router.get("/lists/{list_id}/exports/{export_id}/download")
def download_export(collection_id: str, list_id: str, export_id: str):
    return FileResponse(
        exports.download(collection_id, list_id, export_id),
        filename="Q&A.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/lists/{list_id}/materials")
def list_materials(collection_id: str, list_id: str):
    return materials.list_materials(collection_id, list_id)


def _schedule_material_parse(background, collection_id, list_id, item):
    if item["parse_state"] == "queued":
        background.add_task(
            materials.process, collection_id, list_id, item["id"], item["version_id"]
        )


@router.post("/lists/{list_id}/materials")
def upload_material(
    collection_id: str,
    list_id: str,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
):
    with store.connection(collection_id, "update") as conn:
        store.get_list(conn, list_id)
    result = materials.upload(
        collection_id, list_id, file.filename or "", file.file.read(materials.MAX_SIZE + 1)
    )
    _schedule_material_parse(background, collection_id, list_id, result)
    return result


@router.post("/lists/{list_id}/materials/{material_id}/versions")
def replace_material(
    collection_id: str,
    list_id: str,
    material_id: str,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    expected_revision: Annotated[int, Form()],
):
    with store.connection(collection_id, "update") as conn:
        store.get_list(conn, list_id)
    result = materials.upload(
        collection_id,
        list_id,
        file.filename or "",
        file.file.read(materials.MAX_SIZE + 1),
        material_id,
        expected_revision,
    )
    _schedule_material_parse(background, collection_id, list_id, result)
    return result


@router.post("/lists/{list_id}/materials/{material_id}/retry")
def retry_material(
    collection_id: str,
    list_id: str,
    material_id: str,
    background: BackgroundTasks,
    version_id: str | None = None,
):
    item = materials.queue_retry(collection_id, list_id, material_id, version_id)
    _schedule_material_parse(background, collection_id, list_id, item)
    return {"ok": True, "version_id": item["version_id"]}


@router.patch("/lists/{list_id}/materials/{material_id}")
def archive_material(collection_id: str, list_id: str, material_id: str, data: m.MaterialPatch):
    return materials.archive(
        collection_id, list_id, material_id, data.expected_revision, data.archived
    )


@router.get("/lists/{list_id}/materials/{material_id}/versions/{material_version_id}/file")
def material_file(collection_id: str, list_id: str, material_id: str, material_version_id: str):
    row, path = materials.source(collection_id, list_id, material_id, material_version_id)
    return FileResponse(path, filename=row["filename"])


@router.get("/lists/{list_id}/materials/{material_id}/versions/{material_version_id}")
def material_version(collection_id: str, list_id: str, material_id: str, material_version_id: str):
    return materials.version(collection_id, list_id, material_id, material_version_id)


@router.get("/sources/project/{file_id}/versions/{version_id}")
def project_source_file(collection_id: str, file_id: str, version_id: str):
    from pathlib import Path

    from src.file_mgmt.storage_paths import resolve_version_blob

    with store.connection(collection_id) as conn:
        row = conn.execute(
            "SELECT storage_file_id FROM file_versions WHERE file_id=? AND version_id=?",
            (file_id, version_id),
        ).fetchone()
        if not row:
            store.fail("source_unavailable", 404)
        path = resolve_version_blob(collection_id, file_id, version_id, row[0])
        if path is None:
            store.fail("source_unavailable", 404)
        return FileResponse(path, filename=Path(row[0]).name)


@router.get("/lists/{list_id}/imports")
def list_imports(collection_id: str, list_id: str):
    import json

    with store.connection(collection_id) as conn:
        store.get_list(conn, list_id)
        return [
            {**json.loads(r["plan_json"]), "committed_version_id": r["committed_version_id"]}
            for r in conn.execute(
                "SELECT * FROM qa_imports WHERE list_id=? ORDER BY created_at DESC", (list_id,)
            )
        ]


@router.post("/lists/{list_id}/imports/{import_id}/corrections")
def correction_preview(collection_id: str, list_id: str, import_id: str, data: m.CorrectionPreview):
    return corrections.preview(collection_id, list_id, import_id, data)


@router.post("/lists/{list_id}/imports/{import_id}/corrections/apply")
def correction_apply(collection_id: str, list_id: str, import_id: str, data: m.CorrectionApply):
    return corrections.apply(collection_id, list_id, import_id, data.preview_id)


@router.get("/imports/pending")
def pending_imports(collection_id: str):
    with store.connection(collection_id, "update") as conn:
        return [
            {
                "list_id": r["list_id"],
                "title": r["title"],
                "plan": imports.review_plan(
                    conn, r["list_id"], imports.get_plan(conn, r["list_id"], r["id"])
                ),
            }
            for r in conn.execute("""
            SELECT i.*,l.title FROM qa_imports i JOIN qa_lists l ON l.id=i.list_id
            WHERE i.committed_version_id IS NULL AND i.base_revision=l.revision AND l.archived=0
            AND coalesce(json_extract(i.plan_json,'$.cancelled'),0)=0 ORDER BY i.created_at DESC""")
        ]


@router.delete("/lists/{list_id}/imports/{import_id}")
def cancel_import(collection_id: str, list_id: str, import_id: str, plan_revision: int):
    import json

    with store.connection(collection_id, "update") as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        plan = imports.get_plan(conn, list_id, import_id)
        if plan["committed_version_id"] or plan["plan_revision"] != plan_revision:
            store.fail("stale_import")
        plan["cancelled"] = True
        plan["plan_revision"] += 1
        conn.execute(
            "UPDATE qa_imports SET plan_json=? WHERE id=?",
            (json.dumps(plan, ensure_ascii=False), import_id),
        )
    return {"ok": True}


@router.post("/lists/{list_id}/questions/{question_id}/annotate")
def annotate_question(collection_id: str, list_id: str, question_id: str, data: m.Mutation):
    return intelligence.annotate(collection_id, list_id, question_id, data.expected_revision)


@router.post("/lists/{list_id}/annotations")
def annotate_list(collection_id: str, list_id: str, data: m.Mutation):
    from .pipeline import summarize

    with store.connection(collection_id, "update") as conn:
        listing = store.get_list(conn, list_id)
        if listing["archived"] or data.version_id != listing["current_version_id"]:
            store.fail("read_only", 403)
        revision = listing["revision"]
    summarize(collection_id, list_id, revision)
    return service.detail(collection_id, list_id)


@router.patch("/lists/{list_id}/options")
def options(collection_id: str, list_id: str, data: m.OptionsPatch):
    return metadata.update_options(collection_id, list_id, data)
