# Deploy SinkDuce (pull image)

Source code stays at the **repository root** (`src/`, `frontend/`, …). Nothing is moved into a subfolder, so project paths stay intact.

## Layout A — pull image only (recommended)

```text
my-sinkduce/
  docker-compose.yml    # copy from repo root (image: jethrohou/sinkduce)
  data/                 # created on first run
```

```bash
mkdir -p my-sinkduce/data && cd my-sinkduce
# copy docker-compose.yml from the SinkDuce repo root, then:
# Optional: docker login   # anonymous Hub pulls are often rate-limited; login is usually much faster
docker compose pull
docker compose up -d
```

Open `http://localhost:18900` → **Download local models** in the UI (fetches the ONNX GitHub Release).

Optional env (`.env`):

```bash
SINKDUCE_IMAGE=jethrohou/sinkduce
SINKDUCE_IMAGE_TAG=latest    # or 1.0.3 / v1.0.3 when published
API_PORT=18900
```

> Image must exist on Docker Hub first (`jethrohou/sinkduce`). Until a tag is published, use Layout B.

## Layout B — optional source build as `SinkDuce-build`

```text
my-sinkduce/
  docker-compose.yml       # pull image (optional if you only build)
  data/                    # shared data volume
  SinkDuce-build/          # git clone of this repository
    docker-compose.build.yml
    src/ frontend/ Dockerfile …
```

```bash
mkdir -p my-sinkduce/data
cd my-sinkduce
git clone https://github.com/superdd-coder/SinkDuce.git SinkDuce-build
cd SinkDuce-build
# Build from source; keep data next to the outer folder
SINKDUCE_DATA=../data docker compose -f docker-compose.build.yml up -d --build
```

Or develop **only** inside the clone (data under the repo):

```bash
git clone https://github.com/superdd-coder/SinkDuce.git
cd SinkDuce
docker compose -f docker-compose.build.yml up -d --build
```

## Which file?

| Goal | Command |
|------|---------|
| Run pre-built image | `docker compose up -d` (`docker-compose.yml`) |
| Build from this repo | `docker compose -f docker-compose.build.yml up -d --build` |
