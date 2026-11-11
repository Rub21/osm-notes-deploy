# Website + API: the map of classified notes (/, React app in map/, built in
# the first stage), the labeling page (/label) and /api.
# Connects as the limited `labeler` Postgres role.
FROM node:22-slim AS map
WORKDIR /map
COPY map/package.json map/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY map/ ./
RUN npm run build

FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "fastapi==0.115.*" \
    "uvicorn[standard]==0.32.*" \
    "python-multipart==0.0.*" \
    "psycopg[binary,pool]==3.2.*" \
    "httpx==0.27.*" \
    "itsdangerous==2.2.*"

COPY app.py /app/app.py
COPY static/ /app/static/
COPY --from=map /map/dist/ /app/static/map/
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
