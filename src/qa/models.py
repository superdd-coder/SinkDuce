"""Validated input contracts for Q&A mutations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Status = str
QuestionType = str


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateList(Input):
    title: str = Field(min_length=1, max_length=200)
    columns: list[str] = Field(default_factory=lambda: ["Reply"], max_length=100)
    mode: Literal["blank", "import"] = "blank"

    @field_validator("title")
    @classmethod
    def title_nonempty(cls, value):
        if not value.strip():
            raise ValueError("Title is required")
        return value.strip()

    @field_validator("columns")
    @classmethod
    def column_labels(cls, values):
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("Reply column labels must contain 1–200 characters")
        return [v.strip() for v in values]


class Mutation(Input):
    expected_revision: int = Field(ge=1)
    version_id: str | None = None


class ListPatch(Mutation):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None
    template_id: str | None = None


class ListDelete(Input):
    expected_revision: int = Field(ge=1)
    confirm_title: str = Field(min_length=1, max_length=200)


class QuestionCreate(Mutation):
    text: str = Field(min_length=1, max_length=32767)
    type: QuestionType = "Other"
    external_number: str = Field(default="", max_length=200)
    group_id: str | None = None
    parent_id: str | None = None


class OutlineCreate(Mutation):
    kind: Literal["group", "note"]
    text: str = Field(min_length=1, max_length=32767)
    group_id: str | None = None
    before_id: str | None = None


class OutlineMove(Mutation):
    group_id: str | None = None
    parent_id: str | None = None
    before_id: str | None = None


class OutlinePatch(Mutation):
    text: str | None = Field(default=None, min_length=1, max_length=32767)
    remove: bool = False
    replacement_id: str | None = None


class QuestionPatch(Mutation):
    text: str | None = Field(default=None, min_length=1, max_length=32767)
    status: Status | None = None
    type: QuestionType | None = None
    external_number: str | None = Field(default=None, max_length=200)


class Membership(Mutation):
    included: bool
    restore_replies: bool = False


class ColumnsCreate(Mutation):
    labels: list[str] = Field(min_length=1, max_length=100)

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, values):
        return CreateList.column_labels(values)


class ColumnPatch(Mutation):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    included: bool | None = None
    restore_replies: bool = False


class ReplyPut(Mutation):
    body: str = Field(max_length=32767)
    base_cell_revision_id: str | None = None
    draft_revision: int | None = Field(default=None, ge=1)


class DraftPut(Input):
    version_id: str
    body: str = Field(max_length=32767)
    draft_revision: int = Field(ge=0)
    base_cell_revision_id: str
    proposal_id: str | None = None


class DraftGenerate(Mutation):
    instructions: str = Field(default="", max_length=32767)
    guidance: str | None = Field(default=None, max_length=32767)
    previous_proposal_id: str | None = Field(default=None, max_length=200)
    material_version_ids: list[str] = Field(default_factory=list, max_length=8)


class ImportCommit(Input):
    plan_revision: int = Field(ge=1)


class FieldMapping(Input):
    question: int = Field(ge=0)
    number: int | None = Field(default=None, ge=0)
    replies: list[int] = Field(max_length=100)
    status: int | None = Field(default=None, ge=0)
    type: int | None = Field(default=None, ge=0)
    group: int | None = Field(default=None, ge=0)
    extras: list[int] = Field(default_factory=list, max_length=100)
    ignored: list[int] = Field(default_factory=list, max_length=100)


class TableRegion(Input):
    start_column: int = Field(ge=1, le=100)
    end_column: int = Field(ge=1, le=100)
    header_end_row: int = Field(ge=1, le=2020)
    end_row: int = Field(ge=1, le=2020)


class ImportPatch(ImportCommit):
    rebase: bool = False
    sheet: str | None = None
    header_row: int | None = Field(default=None, ge=1, le=2020)
    mapping: FieldMapping | None = None
    region: TableRegion | None = None
    row_actions: (
        dict[
            str,
            Literal[
                "question",
                "child",
                "section",
                "note",
                "header",
                "continuation",
                "exclude",
                "review",
            ],
        ]
        | None
    ) = None
    parents: dict[str, str] | None = None
    matches: dict[str, str | None] | None = None
    column_matches: dict[str, str | None] | None = None
    content_choices: dict[str, Literal["keep", "incoming"]] | None = None
    blank_columns: list[str] | None = Field(default=None, max_length=100)
    retained_questions: list[str] | None = None
    removed_questions: list[str] | None = None


class ExportCreate(Input):
    expected_revision: int = Field(ge=1)
    include_number: bool = True
    include_status: bool = False
    include_type: bool = False
    resequence: bool = False


class MaterialPatch(Input):
    expected_revision: int = Field(ge=1)
    archived: bool


class CorrectionPreview(Mutation):
    row_key: str
    target: str
    drafts: Literal["keep", "move"] = "keep"


class CorrectionApply(Input):
    preview_id: str


class TemplateInput(Input):
    name: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=200)
    expected_revision: int | None = Field(default=None, ge=1)
    logo_id: str | None = None
    logo_position: Literal["left", "right"] | None = None
    metadata_position: Literal["left", "right"] | None = None
    include_number: bool | None = None
    include_status: bool | None = None
    include_type: bool | None = None
    resequence: bool | None = None


class ListOption(Input):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    color: str = Field(default="#397b60", pattern=r"^#[0-9a-fA-F]{6}$")


class ListOptions(Input):
    statuses: list[ListOption] = Field(max_length=200)
    types: list[ListOption] = Field(max_length=200)


class OptionsPatch(Mutation):
    options: ListOptions
    replacements: dict[str, dict[str, str]] = Field(default_factory=dict)
