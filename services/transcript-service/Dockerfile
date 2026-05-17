FROM python:3.11-slim

WORKDIR /app

# Only install basic essentials if needed. 
# Currently, worker.py uses pure python (wave/io) for audio.
# No extra system packages required.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "worker.py"]
