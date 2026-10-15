# backend/Dockerfile: the API and library; no monorepo, no Census spreadsheets (the image carries
# the generated data). Build context is the repo root (see ../compose.yaml). The same image runs the
# API (the default command) and the deploy-time database build (`python -m jev_naics.db_cli build`).
FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
COPY backend/evals ./evals
COPY backend/transcripts ./transcripts
RUN pip install --no-cache-dir -e ".[api]"
# Run unprivileged (JEV-56). The code stays root-owned and read-only to this user; nothing is
# written at run time (JEV_DECIDER=record is a developer step, never run in the image).
RUN groupadd --system --gid 10001 jev && useradd --system --uid 10001 --gid jev --no-create-home \
      --home-dir /nonexistent --shell /usr/sbin/nologin jev
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8095
CMD ["python", "-m", "jev_naics", "--serve"]
