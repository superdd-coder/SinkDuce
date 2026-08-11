# Third-Party Notices

SinkDuce itself is licensed under the **GNU Affero General Public License v3.0 or later**
(see [`LICENSE`](./LICENSE)).

This document acknowledges **open-source third-party components** that SinkDuce depends on
or redistributes (source tree, frontend production bundle, Python environment, and/or the
official Docker image). It is provided for attribution and license-compliance purposes.

> **Scope**
>
> - Lists **direct** runtime / production dependencies declared in `pyproject.toml` and
>   `frontend/package.json`, plus selected system packages and services used by the
>   official Docker Compose stack.
> - Transitive dependencies are resolved by the package managers (`uv`/`pip`, `pnpm`) and
>   retain their own license files in the installed packages. A full machine-readable dump
>   can be regenerated with:
>
>   ```bash
>   # Python (installed env)
>   # use pip-licenses or similar against the venv if needed
>
>   # Frontend production deps
>   cd frontend && pnpm licenses list --prod
>   ```
>
> - **Remote commercial APIs** (e.g. OpenAI, Cohere, DashScope, MinerU cloud) are optional
>   network services. Only their **client libraries** (if any) are open-source dependencies;
>   use of the APIs is governed by each provider’s terms of service, not by this notice.

---

## Summary

| Category | Open source? | Typical licenses |
|----------|--------------|------------------|
| Python runtime deps | Yes | MIT, BSD-2/3, Apache-2.0, 0BSD, MIT-CMU |
| Python optional (`diarization`) | Yes | MIT, Apache-2.0 / BSD multi, ISC, BSD-3 |
| Frontend production deps | Yes | MIT, Apache-2.0, ISC, OFL-1.1, … |
| Docker system packages | Yes | Apache-2.0 (Tesseract), LGPL/GPL (FFmpeg builds) |
| Qdrant (Compose service) | Yes | Apache-2.0 |

No proprietary / closed-source **library** is declared as a direct dependency of this project.

---

## 1. Python — direct dependencies (`pyproject.toml`)

| Package | License (as published) | Project |
|---------|------------------------|---------|
| fastapi | MIT | https://github.com/fastapi/fastapi |
| uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| pydantic | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | MIT | https://github.com/pydantic/pydantic-settings |
| PyYAML | MIT | https://github.com/yaml/pyyaml |
| qdrant-client | Apache-2.0 | https://github.com/qdrant/qdrant-client |
| openai | Apache-2.0 | https://github.com/openai/openai-python |
| cohere | MIT | https://github.com/cohere-ai/cohere-python |
| mcp | MIT | https://modelcontextprotocol.io |
| pdfplumber | MIT | https://github.com/jsvine/pdfplumber |
| mammoth | BSD-2-Clause | https://github.com/mwilliamson/python-mammoth |
| python-docx | MIT | https://github.com/python-openxml/python-docx |
| openpyxl | MIT | https://openpyxl.readthedocs.io |
| python-pptx | MIT | https://github.com/scanny/python-pptx |
| Markdown | BSD-3-Clause | https://Python-Markdown.github.io/ |
| chardet | 0BSD | https://github.com/chardet/chardet |
| beautifulsoup4 | MIT | https://www.crummy.com/software/BeautifulSoup/ |
| markdownify | MIT | https://github.com/matthewwithanm/python-markdownify |
| tiktoken | MIT | https://github.com/openai/tiktoken |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| pytesseract | Apache-2.0 | https://github.com/madmaze/pytesseract |
| Pillow | MIT-CMU (HPND-derived) | https://github.com/python-pillow/Pillow |
| regex | Apache-2.0 AND CNRI-Python | https://github.com/mrabarnett/mrab-regex |
| dashscope | Apache-2.0 | https://github.com/aliyun/alibabacloud-dashscope-sdk / Aliyun DashScope |

### Optional: `asr-export` / `diarization` extra (host export only; not Docker runtime)

| Package | License (as published) | Project |
|---------|------------------------|---------|
| funasr | MIT | https://github.com/modelscope/FunASR |
| torch | Apache-2.0 (with additional third-party notices) | https://github.com/pytorch/pytorch |
| torchaudio | BSD | https://github.com/pytorch/audio |
| librosa | ISC | https://github.com/librosa/librosa |
| soundfile | BSD-3-Clause | https://github.com/bastibe/python-soundfile |

### Dev / build (not required at runtime)

| Package | License |
|---------|---------|
| pytest, pytest-asyncio, pytest-httpx | MIT |
| ruff | MIT |
| hatchling | MIT |

---

## 2. Frontend — direct production dependencies (`frontend/package.json`)

| Package | License (as published) |
|---------|------------------------|
| react, react-dom | MIT |
| react-router-dom | MIT |
| zustand | MIT |
| tailwindcss, @tailwindcss/vite, tailwind-merge, tw-animate-css | MIT |
| @base-ui/react | MIT |
| @dnd-kit/core, @dnd-kit/sortable, @dnd-kit/utilities | MIT |
| @tiptap/* (core, react, starter-kit, extensions, pm) | MIT |
| tiptap-markdown | MIT |
| react-markdown, remark-gfm, remark-breaks, rehype-raw, rehype-stringify | MIT |
| react-syntax-highlighter | MIT |
| lucide-react | ISC |
| class-variance-authority | Apache-2.0 |
| clsx | MIT |
| sonner | MIT |
| shadcn | MIT |
| @file-viewer/react, @file-viewer/preset-office, @file-viewer/renderer-text, @file-viewer/vite-plugin | Apache-2.0 |
| **@fontsource-variable/geist** | **OFL-1.1** (SIL Open Font License) |
| **@fontsource-variable/source-serif-4** | **OFL-1.1** (SIL Open Font License) |

### Fonts (OFL-1.1) — attribution required when redistributing font software

These fonts are bundled with the frontend and redistributed under the SIL Open Font License 1.1.
The OFL requires that copyright notices and the license text accompany redistributed Font Software.

| Font package | Copyright / project |
|--------------|---------------------|
| Geist (`@fontsource-variable/geist`) | Copyright 2024 The Geist Project Authors — https://github.com/vercel/geist-font |
| Source Serif 4 (`@fontsource-variable/source-serif-4`) | Adobe / Source Serif — via Fontsource |
| Noto Sans SC (`@fontsource-variable/noto-sans-sc`, transitive via `@file-viewer/renderer-pdf`) | Google Noto — via Fontsource |

Full OFL text is included with each package under `frontend/node_modules/@fontsource-variable/*/LICENSE`.

### Notable transitive frontend licenses

| Package | License | Note |
|---------|---------|------|
| pdfjs-dist (via file-viewer) | Apache-2.0 | PDF rendering |
| lightningcss | MPL-2.0 | File-level copyleft; used as-is |
| dompurify | MPL-2.0 OR Apache-2.0 | Dual-licensed |
| jszip | MIT OR GPL-3.0-or-later | Dual-licensed; project may use under MIT |
| caniuse-lite | CC-BY-4.0 | Browser compatibility data (primarily via tooling chain) |
| khroma | MIT (upstream; package metadata omits license field) | https://github.com/fabiospampinato/khroma |

---

## 3. System packages (official Docker image)

The application Docker image installs:

| Package | Typical license | Purpose |
|---------|-----------------|---------|
| tesseract-ocr (+ eng / chi-sim data) | Apache-2.0 | OCR fallback |
| ffmpeg | LGPL / GPL (build-dependent) | Audio/video processing for meeting features |
| libgl1 | LGPL (Mesa / related) | Graphics support for native libs |

Base images:

| Image | Notes |
|-------|--------|
| `python:3.11-slim` | PSF / Debian packages (various open-source licenses) |
| `node:20-slim` | Build stage only |
| `ghcr.io/astral-sh/uv` | Build tooling (Apache-2.0 / MIT — see Astral uv project) |

---

## 4. Infrastructure services (Docker Compose)

| Component | License | Role |
|-----------|---------|------|
| [Qdrant](https://github.com/qdrant/qdrant) (`qdrant/qdrant`) | Apache-2.0 | Vector database |

Qdrant runs as a **separate container**. It is not linked into the SinkDuce binary; it is an
optional/required peer service for the default deployment.

---

## 5. License compatibility notes (informational)

1. **SinkDuce is AGPL-3.0-or-later.** Combining AGPL-covered application code with
   permissive dependencies (MIT/BSD/Apache-2.0/ISC/0BSD) is a common and generally accepted
   practice. Redistributors must still honor **each** dependency’s notice and attribution
   requirements in addition to AGPL obligations.
2. **Apache-2.0** components: retain copyright, patent, trademark, and attribution notices;
   include any upstream `NOTICE` file content if present when redistributing.
3. **OFL-1.1 fonts**: may be embedded and redistributed with the app; do not sell the fonts
   by themselves; keep OFL notices with the font files.
4. **MPL-2.0** (e.g. lightningcss): file-level copyleft applies to modified MPL-covered files
   if those modified files are distributed.
5. **FFmpeg**: ensure the binary shipped in your deployment matches the license of the build
   you redistribute (Debian packages document this under `/usr/share/doc/ffmpeg/`).

This file is **not legal advice**. For production redistribution or dual-licensing questions,
consult qualified counsel.

---

## 6. Regenerating this inventory

When adding or upgrading dependencies:

1. Confirm the package is open source and note its SPDX license.
2. Update the tables in **§1** / **§2** for new **direct** dependencies.
3. If a dependency uses **OFL**, **Apache-2.0 with NOTICE**, **MPL**, **LGPL**, or **GPL**,
   call it out explicitly (as fonts and FFmpeg are above).
4. Re-run `cd frontend && pnpm licenses list --prod` and skim for unexpected
   proprietary / non-OSI licenses.

Last reviewed: **2026-08-02** (direct deps from `pyproject.toml` / `frontend/package.json` on branch `feature/file-management-v2`).
