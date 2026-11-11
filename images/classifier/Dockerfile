# Classifies open notes on CPU with the ONNX int8 models (no GPU, no PyTorch).
# Models are mounted from ./models/classifier, not built into the image.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "psycopg[binary]==3.2.*" \
    "numpy<3" \
    "onnxruntime==1.31.*" \
    "tokenizers==0.23.*"

COPY classify.py /app/
WORKDIR /app

ENTRYPOINT ["python", "/app/classify.py"]
