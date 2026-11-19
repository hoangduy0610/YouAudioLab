FROM python:3.12.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.docker \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Pin the uv binary so lock installs are reproducible across rebuilds.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && mkdir -p /opt/youaudiolab \
    && ffmpeg -version > /opt/youaudiolab/ffmpeg-version.txt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install from the frozen lock before copying the rest of the tree so dependency
# layers stay cacheable. Hatchling needs the package sources listed in
# pyproject.toml ([tool.hatch.build.targets.wheel] packages).
COPY pyproject.toml uv.lock README.md /app/
COPY manage.py /app/manage.py
COPY config /app/config
COPY apps /app/apps

RUN uv sync --frozen --extra dev --no-cache

COPY templates /app/templates
COPY static /app/static
COPY docs /app/docs
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && cp /opt/youaudiolab/ffmpeg-version.txt /app/FFMPEG_BUILD.txt

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8001"]
