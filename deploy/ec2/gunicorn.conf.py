# Gunicorn config for FastAPI + Uvicorn workers (Amazon Linux 2023 / systemd).
# Paths assume repo root at /opt/saathi — override by editing or using a different WorkingDirectory.
bind = "127.0.0.1:8000"
# ~2 vCPUs: one worker is the manager; 3 Uvicorn workers is a good fit for t3.small and similar.
workers = 3
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# Not using gunicorn threads (async workers handle concurrency)
threads = 1
