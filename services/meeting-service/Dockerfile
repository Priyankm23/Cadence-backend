FROM python:3.11-slim

WORKDIR /app

# Install CPU-only version of PyTorch to avoid massive NVIDIA/CUDA downloads
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:sio_app", "--host", "0.0.0.0", "--port", "8002"]
