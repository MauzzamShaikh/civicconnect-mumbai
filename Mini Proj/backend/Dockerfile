FROM python:3.11-slim
WORKDIR /app

# Keep the largest, slowest-changing wheels in a layer that is independent of
# either requirements file. requirements-ml.txt also pins these exact versions.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout=120 --retries=5 \
      torch==2.5.1 torchvision==0.20.1 \
      --index-url https://download.pytorch.org/whl/cpu

COPY requirements-ml.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout=120 --retries=5 -r requirements-ml.txt

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout=120 --retries=5 -r requirements.txt

COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
