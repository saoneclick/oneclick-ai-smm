FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      wget gnupg ca-certificates fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir playwright fastapi uvicorn

RUN playwright install --with-deps chromium

WORKDIR /app
COPY app.py /app/app.py
COPY fonts /app/fonts

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
