# Labeling API + page: serve note batches by language, save labels,
# export/import CSV. Connects as the limited `labeler` Postgres role.
# Also serves the map viewer (/map), a React app built in the first stage.
FROM node:22-slim AS viewer
WORKDIR /viewer
COPY viewer/package.json viewer/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY viewer/ ./
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
COPY --from=viewer /viewer/dist/ /app/static/map/
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
