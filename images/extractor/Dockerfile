# Extracts notes (whole planet) from the ingestion DB, detects language and writes
# a stratified sample to CSV for labeling.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "psycopg[binary]==3.2.*" \
    "pandas==2.2.*" \
    "numpy<2" \
    "fasttext-wheel==0.9.2"

# fastText language ID model (176 languages, compressed version, ~1 MB).
ADD https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz /models/lid.176.ftz

COPY extract.py /app/extract.py
WORKDIR /app

ENTRYPOINT ["python", "/app/extract.py"]
