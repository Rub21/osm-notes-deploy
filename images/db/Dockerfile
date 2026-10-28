# PostgreSQL 18 + PostGIS 3, multi-arch (amd64 and arm64).
# postgis/postgis has no arm64 build, so we add PostGIS on top of the official image.
ARG PG_MAJOR=18
FROM postgres:${PG_MAJOR}-bookworm

ARG PG_MAJOR=18
RUN apt-get update \
 && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}-postgis-3" \
 && rm -rf /var/lib/apt/lists/*

COPY initdb/ /docker-entrypoint-initdb.d/
