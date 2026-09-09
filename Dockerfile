# Krungsri ReadyCall — the "any judge can run this" image (`D136`, Track E).
#
#   docker build -t readycall .
#   docker run --rm -p 8000:8000 readycall
#   then http://127.0.0.1:8000/sim and http://127.0.0.1:8000/workstation
#
# ⚠️ THIS IMAGE SHIPS ON `STT_ENGINE=scripted`, ON PURPOSE. A plain container cannot reach
# the GPU without host setup that varies by machine, and a venue is exactly where that
# fails. The real Typhoon engine runs on our own box for the pitch and the recorded video;
# this image is the one that starts anywhere. Everything else about the system is real —
# the IVR, the matcher, the brief, the workstation, the paired customer screen and the
# broker's whole job all run here with no services, no keys and no GPU.
#
# ⚠️ It also defaults to `LLM_PROVIDER=rulebased`, which is a real adapter and needs no
# key (`D119`). Pass one at run time to turn the model on:
#   docker run --rm -p 8000:8000 \
#     -e LLM_PROVIDER=anthropic -e ANTHROPIC_API_KEY=sk-ant-... \
#     -e LLM_FAST_PROVIDER=openai_compatible -e LLM_FAST_MODEL=gpt-5.4-mini \
#     -e LLM_FAST_BASE_URL=https://api.openai.com/v1 -e OPENAI_API_KEY=sk-proj-... \
#     readycall

# --- stage 1: the workstation bundle ---------------------------------------------------
#
# `apps/**/dist/` is gitignored (`Q17`), so the image has to build it rather than copy it.
# That also settles the venue-with-no-internet worry for anyone using this image: the
# bundle is baked in at build time, so the container needs no npm at run time.
FROM node:22-alpine AS workstation

WORKDIR /build
COPY apps/workstation/package.json apps/workstation/package-lock.json ./
RUN npm ci
COPY apps/workstation/ ./
RUN npm run build


# --- stage 2: the runtime --------------------------------------------------------------
FROM python:3.11-slim AS runtime

# uv is pinned: the lockfile is resolved by a specific version and `--frozen` is only a
# real check if the resolver agrees with the one that wrote it.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

# ⚠️ THE USER IS CREATED BEFORE ANYTHING IS WRITTEN, and that is a BUILD-TIME decision as
# much as a security one. The first version ended with `chown -R readycall /app`, which
# walks the ~4,000 bytecode-compiled files in the venv and cost **109 seconds on every
# rebuild** — on a feature-freeze day that is the difference between iterating and not.
# Creating the user first and copying with `--chown` costs nothing at all.
RUN useradd --create-home --uid 10001 readycall
WORKDIR /app
RUN chown readycall:readycall /app
USER readycall

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Dependencies first, so an edit to `src/` does not re-resolve the world.
#
# ⚠️ EVERY EXTRA GOES IN ONE COMMAND. `uv sync` PRUNES (`B31`) — a second `uv sync` naming
# one extra silently removes the others, which on the dev laptop removed the whole GPU
# stack and exposed five latent failures. `web` is required (nothing serves without it)
# and `llm` is what lets a key switch the models on at run time. `ml`/`asr` are
# deliberately absent: ~3 GB of CUDA wheels for an engine this image does not run.
COPY --chown=readycall:readycall pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra web --extra llm --no-install-project

# `WORKSTATION_DIST` resolves to `<parent of src>/apps/workstation/dist` (`api/app.py`),
# and `config_dir` / `prompt_dir` are read relative to the working directory — so the
# layout below is load-bearing, not tidiness.
COPY --chown=readycall:readycall src/ ./src/
COPY --chown=readycall:readycall config/ ./config/
COPY --chown=readycall:readycall prompts/ ./prompts/
COPY --chown=readycall:readycall mock/ ./mock/
COPY --chown=readycall:readycall alembic.ini ./
COPY --from=workstation --chown=readycall:readycall /build/dist ./apps/workstation/dist
# ⚠️ BOTH STATIC CUSTOMER PAGES, AND LEAVING THEM OUT FAILS SILENTLY. `create_app` mounts
# `/sim` and `/assist/{token}` only `if <dir>.is_dir()` — which is right for a dev clone
# with no built workstation, and means a container missing these starts, reports healthy,
# serves `/workstation`, and 404s two of the three customer surfaces with no error
# anywhere. Caught by curling `/sim` in the running container, not by the build (`D136`).
COPY --chown=readycall:readycall apps/customer_sim/ ./apps/customer_sim/
COPY --chown=readycall:readycall apps/customer_assist/ ./apps/customer_assist/

RUN uv sync --frozen --no-dev --extra web --extra llm

# The defaults that make this image start with nothing configured. Each is overridable
# with `-e`; none of them is a behaviour knob nobody reads (`Q26`) — every one is read by
# `Settings` and changes what the container does.
ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    STT_ENGINE=scripted \
    LLM_PROVIDER=rulebased \
    STORAGE_BACKEND=memory \
    BLOB_STORAGE=memory \
    LOG_FORMAT=json \
    ENV=demo

EXPOSE 8000

# `/health` names the app and reports `intents_loaded`, which is the check that catches a
# container that started but loaded no domain pack.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys,json; \
d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)); \
sys.exit(0 if d.get('status')=='ok' and d.get('intents_loaded') else 1)"

CMD ["uv", "run", "--no-sync", "python", "-m", "readycall.entrypoints.api"]
