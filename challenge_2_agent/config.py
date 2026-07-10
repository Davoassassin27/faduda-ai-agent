"""
config.py — Configuración del Agente Autónomo para Google Forms.

Lee credenciales desde .env: Gemini API, Google Sheets, URLs de formularios.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


@dataclass
class GeminiConfig:
    api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class SheetsConfig:
    """Google Sheets lectura — requiere service account."""
    credentials_path: str = os.getenv("GSHEETS_CREDENTIALS_PATH", "./service_account.json")
    spreadsheet_id: str = os.getenv("GSHEETS_SPREADSHEET_ID", "")

    @property
    def configured(self) -> bool:
        return bool(self.spreadsheet_id) and Path(self.credentials_path).exists()

    def load_credentials(self) -> dict[str, Any]:
        path = Path(self.credentials_path)
        if not path.exists():
            raise FileNotFoundError(f"Service account no encontrado: {path}")
        with open(path) as f:
            return json.load(f)


@dataclass
class FormsConfig:
    form_1_url: str = os.getenv(
        "FORM_1_URL",
        "https://forms.gle/oqjtULJ6iGBT7HFR7",
    )
    form_2_url: str = os.getenv(
        "FORM_2_URL",
        "https://forms.gle/JQTABscuZxn2S6Dh7",
    )


@dataclass
class AppConfig:
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    sheets: SheetsConfig = field(default_factory=SheetsConfig)
    forms: FormsConfig = field(default_factory=FormsConfig)

    @classmethod
    def load(cls) -> "AppConfig":
        return cls()
