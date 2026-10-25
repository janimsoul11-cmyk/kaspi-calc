import os
import sys
import tempfile
from pathlib import Path

# Настройки читаются при импорте — задаём окружение до импорта пакета bot.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="assistant-test-"))
os.environ.setdefault("TIMEZONE", "Asia/Almaty")
os.environ.setdefault("MONTHLY_BUDGET_USD", "30")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from bot.db import Store  # noqa: E402


@pytest.fixture
def store():
    return Store(":memory:")
