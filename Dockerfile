# The page hosted live, one process of it for each visitor, each on the visitor's own Anthropic API key
# (harness/host.py). The host sets PORT, and may set the keys and limits harness/host.py names.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY harness harness
COPY knowledge knowledge
COPY web web
RUN uv sync --frozen --no-dev
# What one conversation read about a closed range serves the next (harness/observations.py); a volume keeps it
# across deploys. The sessions' folders go with the container.
VOLUME /app/observations
ENV PATH="/app/.venv/bin:$PATH" EVENT_RISK_SESSIONS_DIR=/tmp/event-risk-sessions
EXPOSE 8000
CMD ["event-risk-host", "--bind", "0.0.0.0"]
