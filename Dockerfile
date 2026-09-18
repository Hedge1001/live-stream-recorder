FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY config ./config
RUN mkdir -p /app/captures
ENV CAPTURE_DIR=/app/captures HOST=0.0.0.0 PORT=5000
EXPOSE 5000
VOLUME ["/app/captures"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "app:app"]
