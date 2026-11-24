# Pinned by index digest (multi-arch) so rebuilds use the same base image even if the
# tag moves. Base is Debian 13 (trixie). To update: re-resolve the tag and change both
# the tag and the digest together.
FROM python:3.12.14-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.docker \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Pin the uv binary (tag and index digest) so lock installs are reproducible across rebuilds.
COPY --from=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc /uv /usr/local/bin/uv

# apt reads a fixed Debian snapshot (snapshot.debian.org) instead of the live mirror, so the
# pinned package versions stay installable after Debian publishes newer builds. Both suites
# use the same timestamp so dependencies resolve against one consistent archive state.
# The snapshot Release files are past their Valid-Until date, hence Check-Valid-Until: no.
# Signatures are still verified with the image's debian-archive-keyring.
ARG DEBIAN_SNAPSHOT=20261124T000000Z
ARG FFMPEG_VERSION=7:7.1.5-0+deb13u1
ARG LIBGOMP_VERSION=14.2.0-19
RUN printf '%s\n' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie' \
      'Components: main' \
      'Check-Valid-Until: no' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      '' \
      'Types: deb' \
      "URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/" \
      'Suites: trixie-security' \
      'Components: main' \
      'Check-Valid-Until: no' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      > /etc/apt/sources.list.d/debian.sources \
    && rm -f /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" "libgomp1=${LIBGOMP_VERSION}" \
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
