import os

# config.settings is built at import time and requires it
os.environ.setdefault("DOWNLOAD_STORAGE_BASE_URL", "https://downloads.test/")
