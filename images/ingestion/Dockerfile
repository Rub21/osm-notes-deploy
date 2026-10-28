# OSM-Notes-Ingestion (AngocA) running as a daemon.
# Clones the repo with its submodule (lib/osm-common), so the image does not
# depend on a local checkout.
FROM ubuntu:24.04

# bash so brace expansion works in RUN.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive TZ=UTC
# Must match the db image major version.
ARG PG_MAJOR=18

# Commands checked by __checkPrereqsCommands (functionsProcess.sh + osm-common),
# plus procps (pgrep/ps) and util-linux (flock/prlimit) used by the daemon.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    gnupg \
    jq bc gawk parallel aria2 \
    bzip2 zip unzip \
    libxml2-utils \
    gdal-bin \
    nodejs npm \
    mutt \
    procps util-linux coreutils \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}" \
 && npm install -g osmtogeojson ajv-cli \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*

ARG INGESTION_REPO=https://github.com/Rub21/OSM-Notes-Ingestion.git
ARG INGESTION_REF=main
RUN git clone --recurse-submodules "${INGESTION_REPO}" /opt/osm-notes-ingestion \
 && cd /opt/osm-notes-ingestion \
 && git checkout "${INGESTION_REF}" \
 && git submodule update --init --recursive \
 && test -f lib/osm-common/commonFunctions.sh \
 && cp etc/properties.sh.example etc/properties.sh

# Installed-mode directories: logs, temp, locks (see install_directories.sh)
# and DATA_DIR for backups (countries, maritimes, noteLocation).
RUN useradd -m -s /bin/bash notes \
 && mkdir -p /var/log/osm-notes-ingestion/{daemon,processing,monitoring} \
             /var/tmp/osm-notes-ingestion/{planet,overpass,api} \
             /var/run/osm-notes-ingestion \
             /var/lib/osm-notes-ingestion \
 && chown -R notes:notes /var/log/osm-notes-ingestion /var/tmp/osm-notes-ingestion \
                         /var/run/osm-notes-ingestion /var/lib/osm-notes-ingestion \
                         /opt/osm-notes-ingestion

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER notes
WORKDIR /opt/osm-notes-ingestion
ENV DATA_DIR=/var/lib/osm-notes-ingestion

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash", "bin/process/processAPINotesDaemon.sh"]
