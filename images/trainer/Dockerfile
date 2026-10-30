# Train and evaluate the notes classifier (SetFit, CPU only).
FROM python:3.12-slim

# CPU-only PyTorch keeps the image small (no CUDA).
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    "setfit==1.1.0" \
    "sentence-transformers==3.3.1" \
    "transformers==4.45.2" \
    "datasets==3.1.0" \
    "scikit-learn==1.5.2" \
    "pandas==2.2.3" \
    "psycopg[binary]==3.2.*"

# Downloaded base models are cached in a volume.
ENV HF_HOME=/cache TOKENIZERS_PARALLELISM=false TQDM_DISABLE=1 HF_HUB_DISABLE_PROGRESS_BARS=1
COPY train.py /app/train.py
WORKDIR /app

ENTRYPOINT ["python", "/app/train.py"]
