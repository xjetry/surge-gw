# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS mihomo
ARG TARGETARCH
ARG MIHOMO_VERSION=v1.19.27
ARG MIHOMO_SHA256_amd64=fb3e34c55844f389ff54679e5a3aec331d5ec38006c20f8dcc476fb47768a58f
ARG MIHOMO_SHA256_arm64=87db0c6660a9557a901b5750f997967e71d8c0af07ea1d1dd4d04c28da7f7e6f
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && asset="mihomo-linux-${TARGETARCH}-${MIHOMO_VERSION}.gz" \
 && curl -fsSL "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${asset}" -o /tmp/mihomo.gz \
 && case "$TARGETARCH" in \
      amd64) echo "${MIHOMO_SHA256_amd64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      arm64) echo "${MIHOMO_SHA256_arm64}  /tmp/mihomo.gz" | sha256sum -c - ;; \
      *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && gunzip -c /tmp/mihomo.gz > /usr/local/bin/mihomo \
 && chmod +x /usr/local/bin/mihomo

FROM python:3.13-slim AS runtime
COPY --from=mihomo /usr/local/bin/mihomo /usr/local/bin/mihomo
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --home-dir /data --no-create-home surgegw \
 && mkdir -p /data \
 && chown surgegw:surgegw /data
USER surgegw
ENV DATA_DIR=/data \
    MIHOMO_BIN=/usr/local/bin/mihomo
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import json,os,urllib.request,sys; p=os.environ.get('HTTP_PORT','8080'); d=json.load(urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=4)); sys.exit(0 if d.get('nodes', 0) > 0 else 1)"]
ENTRYPOINT ["python", "-m", "surge_gw"]
