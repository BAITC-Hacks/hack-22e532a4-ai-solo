from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.getenv("BAQBAQ_DB_PATH", ROOT / "data" / "baqbaq.db"))
    upload_dir: Path = Path(os.getenv("BAQBAQ_UPLOAD_DIR", ROOT / "uploads"))
    model_mode: str = os.getenv("BAQBAQ_MODEL_MODE", "live").strip().lower()
    model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
    demo_before: Path = Path(
        os.getenv(
            "BAQBAQ_DEMO_BEFORE",
            ROOT / 'data' / 'organizer' / 'revision_8.docx',
        )
    )
    demo_after: Path = Path(
        os.getenv(
            "BAQBAQ_DEMO_AFTER",
            ROOT / 'data' / 'organizer' / 'revision_9.docx',
        )
    )

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
