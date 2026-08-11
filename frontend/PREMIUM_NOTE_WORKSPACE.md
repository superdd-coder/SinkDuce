# Premium Note Workspace & Markdown Editor — Style Source of Truth

**Date:** 2026-08-06 (updated 2026-08-06 — full Note dialog locked)  
**Status:** **Canonical.** All future markdown editors and note-like workspaces must follow this.  
**Reference surface:** Note editor dialog (Overview → Notes → open a note)

| Layer | Primary files |
|-------|----------------|
| Workspace shell | `note-editor-dialog.tsx`, `notes-card.tsx`, `.pm-ws-*` in `index.css` |
| Pane chrome + soft doc swap | `note-pane.tsx`, `meeting-summary-panel.tsx` |
| Editor + toolbar + distill | `tiptap-editor.tsx`, `markdown-editor.tsx`, `.pm-fmt-*` / `.distill-block` |
| Menus | `menu.tsx` (`SoftMenu`), `.pm-menu` / `.pm-menu--soft` |

Related: `2026-08-06-distill-block-premium-redesign.md` (block detail).

---

## Principle

Match **Premium SoT**:

- **Chrome:** Geist (`--pm-ff`), quiet green accent (`--pm-green`), soft shadow, white cards on beige canvas  
- **Prose:** Source Serif (`--pm-ff-prose` / `--pm-t-prose`)  
- **Motion:** opacity-first silk; avoid multi-layer shadow thrash and transform on scroll containers  
- **Menus:** SoftMenu language only for float chrome  

Do **not** reintroduce: heavy green capsule toolbars, hard grid chrome, selection-bubble marks that fight table/image floats, empty half-pane “shell then fill” product UX (prefer soft dim/crossfade instead).

---

## 1. Note workspace dialog (shell)

### Geometry & surface

| Token / class | Spec |
|---------------|------|
| Stage | `.pm-ws-dialog` — ~92vw × 85vh, white nested cards on beige stage |
| Cards | `.pm-ws-card` / `--pane` / `--rail` / `--rail-r` — radius `--pm-r-lg`, soft shadow |
| Deck body | `.pm-ws-body` — left list rail + `.pm-ws-panes` |

### Open / close lifecycle (**required**)

Exit animation **only works if** the dialog stays mounted while `open` goes `false`:

1. Parent keeps `noteId` (or equivalent) through close.  
2. Set `open={false}` first → Base UI `data-ending-style` / `data-closed` runs.  
3. After **~340ms** (`--pm-ws-dialog-ms` + buffer), unmount / clear id and refresh list.  

**Reference:** `notes-card.tsx` — `editorOpen` + `activeNoteId` + `EDITOR_CLOSE_MS`.  
**Do not** mount with `open` always `true` and unmount on close (hard cut).

### Close motion CSS

`.pm-ws-dialog`: opacity + `scale` (not `transform`, so centering `-translate-x/y-1/2` is preserved).  
Backdrop fades with portal `:has(.pm-ws-dialog)`.

### Dual pane & rail

| Rule | Spec |
|------|------|
| Max panes | 2; dual **slots always in DOM** (`data-slot="0|1"`, `.is-slot-collapsed`) — avoid 1→2 flex child jump |
| Motion mutex | `phase`: idle → rail_close \| pane_split \| pane_merge \| rail_open → idle |
| Distill rail | Single `railOwnerId`; rides with focused note group; `.has-rail` extra flex-basis keeps **editor cards equal width** |
| Focus chrome | Dual-pane only: opacity ring on slot `::after` (not multi-layer box-shadow thrash) |
| Split button | May close rail first, then split empty second page; Distill-open-beside keeps focus + rail |
| Height pin | Title row fixed height; toolbar row **overflow visible** so SoftMenus are not clipped |

---

## 2. Document host (NotePane pattern)

### Chrome (two rows)

1. **Title row** — Source Serif title + rename pencil (hover) + split/close icons  
2. **Toolbar row** — status (Ingested…) · Detail · Ingest \| Update▾ · ⋮ (Download / Delete)

Update / ⋮: `SoftMenu` absolute under triggers; parent must not `overflow: hidden` on that row; header `z-index` above editor body.

### Soft document switch (no hard flash)

Phase machine on body (not remount TipTap for every switch):

```
idle → out (fade old, ~280ms) → swap content at opacity 0 → in (fade new, ~420ms) → idle
```

| Class | Role |
|-------|------|
| `.is-doc-out` | Opacity → 0 |
| `.is-doc-in` | Enter animation from 0 |
| `.is-doc-idle` | Resting |
| Title | `.is-title-out` / `.is-title-in` same clock |

- Soft switch: **keep previous body** while fetching; flush pending save for previous note.  
- Block `onChange` while loading / swap busy.  
- Prefer in-place TipTap `setContent` over full remount when possible.  
- **No** empty half-pane product path for Distill open (shell-then-fill rejected).  

Meeting panel in the same dialog follows the same swap phases.

### Scroll ownership

| Context | Wheel |
|---------|--------|
| Distill **collapsed** | Drive outer `.pm-ws-editor` (note document), **not** inner clipped body |
| Distill **expanded** + overflow | Inner scroll; contain at ends (do not chain to outer) |

---

## 3. Markdown editor (body)

### Layout

| Layer | Spec |
|-------|------|
| Prose | Source Serif, ink/muted |
| Format strip | Above body; **only while editing** (`.pm-fmt-toolbar.is-editing`) |
| Idle / reading | Toolbar hidden (`opacity: 0`, no pointer events) |
| Host | Prefer external `EditorToolbar` + `showToolbar={false}` + `flush` when title chrome sits above (Note pattern) |
| Narrow bar (&lt; ~420px) | Highlight + text color collapse to single ▾ |

### Format toolbar (R2 strip)

**No section labels.** Order (whisper 3px dots):

1. Bold · Italic · Strike — Lucide ~1.5 stroke  
2. Highlight — half-fill swatches + `+` menu  
3. Text color — light `A` + hairline (Geist 500) + `+` menu  
4. Heading — `H` / `H1`–`H3` SoftMenu  
5. Lists — `List` · `ListOrdered` · `ListTodo`  

**Highlight:** document mark = lower ~45% of glyph (`--pm-hl`); clear “none” only in `+` menu.  
**Text color:** Premium tokens only (Ink / Green / Danger / Muted / Warm).  
**Heading:** Geist SoftMenu; H1–H3 only (no Paragraph row); re-click level → body.

### Interaction stability

- Toolbar visibility from **editor focus** + debounced hide  
- Guard `editor.isDestroyed` on delayed hide / `getAttributes`  
- On note switch: do not transplant selection from previous doc  
- Click **below last block** only → caret at end; left/right margins do not force caret  

### Float chrome (image / table)

- Portal + `position: fixed`; SoftMenu surface  
- Do not add a second marks bubble that competes with node floats  
- Slash menu: SoftMenu language; no Video / Distill in slash (product choice)

### Quote / callout / table

- Soft radius language (table/quote/callout aligned with Premium cards)  
- Avoid hard divider aesthetics  

---

## 4. Distill block (in-editor)

See also dedicated distill spec. Summary:

- White card, neutral hairline, soft shadow  
- Header: grip · source title · two-step delete  
- Expand: D pattern (fade + pill; expanded sticky fade + Show less)  
- Collapsed max-height ~200px, `overflow: hidden` (wheel → outer editor)  
- Expanded: internal scroll + overscroll contain  
- Markdown body via remark GFM; loading non-draggable  

---

## 5. SoftMenu (all float menus)

| Rule | Spec |
|------|------|
| Component | `SoftMenu` + `MenuItem` from `@/components/ui/menu` |
| Motion | `.pm-menu--soft`: opacity + slight Y/scale; `is-open` |
| Duration | `--pm-menu-ms` ~180ms |
| Placement | Prefer absolute under trigger; if ancestor clips, portal or raise overflow/z-index (Note toolbar lesson) |

---

## 6. When adding a new markdown editor surface

1. Reuse `TiptapEditor` / `MarkdownEditor` + shared CSS (`.pm-fmt-*`, mark, distill, SoftMenu).  
2. Prefer **external** toolbar + `flush` when host has its own title chrome (NotePane).  
3. Do **not** reintroduce green capsule `animate-toolbar-float` bar.  
4. Doc switch: soft out → swap → in; no hard blank wipe after first load.  
5. Dialog hosts: `open=false` then delayed unmount for exit motion.  
6. Table / image floats stay node-scoped; SoftMenu + Geist for chrome; Source Serif for prose.  
7. Distill wheel: collapsed → outer editor; expanded → inner.  

---

## 7. Key files (checklist)

| File | Role |
|------|------|
| `note-editor-dialog.tsx` | Dual-slot panes, phase machine, rail owner, Distill-beside |
| `notes-card.tsx` | Open/close lifecycle for exit animation |
| `note-pane.tsx` | Title/toolbar chrome, soft doc swap, ingest/update menus |
| `meeting-summary-panel.tsx` | Same swap language inside workspace |
| `tiptap-editor.tsx` | EditorToolbar, PremiumHighlight, distill NodeView, wheel ownership |
| `markdown-editor.tsx` | Public wrapper (`flush` / toolbar props) |
| `menu.tsx` | SoftMenu shell |
| `index.css` | `.pm-ws-*`, `.pm-fmt-*`, `.distill-block`, `.pm-menu--soft`, dialog open/close |

---

## Approval

- **2026-08-06:** Editor strip + distill language approved as SoT for markdown surfaces.  
- **2026-08-06 (later):** Full Note workspace dialog (panes, rail, soft swap, close motion, scroll ownership, SoftMenu chrome) **locked** as the reference for subsequent markdown / note UIs.  
