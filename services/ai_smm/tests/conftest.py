import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("UPLOADS_DIR", str(Path(__file__).parent / "_uploads"))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test")
os.environ.setdefault("TELEGRAM_RELAY_BASE", "https://example.test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_ADMIN_CHAT_ID", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("DZEN_DIR", str(Path(__file__).parent / "_dzen"))
