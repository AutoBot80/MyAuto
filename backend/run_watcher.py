from app.services.bulk_watcher_service import start_watcher
import time

start_watcher()

print("Watcher running...")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping watcher...")
    