# Gunicorn config for FastAPI + Uvicorn workers (Amazon Linux 2023 / systemd).
# Paths assume repo root at /opt/saathi — override by editing or using a different WorkingDirectory.
import multiprocessing

bind = "127.0.0.1:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200

# Not using gunicorn threads (async workers handle concurrency)
threads = 1
