# Labeling API + page: serve note batches by language, save labels,
# export/import CSV. Connects as the limited `labeler` Postgres role.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "fastapi==0.115.*" \
    "uvicorn[standard]==0.32.*" \
    "python-multipart==0.0.*" \
    "psycopg[binary,pool]==3.2.*"

COPY app.py /app/app.py
COPY static/ /app/static/
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
