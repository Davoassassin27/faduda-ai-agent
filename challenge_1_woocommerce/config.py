"""
config.py — Configuración centralizada con variables de entorno.

Todas las credenciales y parámetros se leen desde .env.
Ningún secreto está hardcodeado en el código.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

logger = logging.getLogger("wc.config")

# Cache de service account para no leer disco en cada ciclo
_GSHEETS_CREDS_CACHE: dict[str, Any] | None = None


@dataclass
class WooCommerceConfig:
    """Credenciales y URL de la API REST de WooCommerce."""
    url: str
    consumer_key: str
    consumer_secret: str

    @classmethod
    def from_env(cls) -> "WooCommerceConfig":
        return cls(
            url=os.getenv("WC_URL", "https://fadua.ar/pruebas").rstrip("/"),
            consumer_key=os.getenv("WC_CONSUMER_KEY", ""),
            consumer_secret=os.getenv("WC_CONSUMER_SECRET", ""),
        )

    @property
    def api_url(self) -> str:
        return self.url


@dataclass
class GoogleSheetsConfig:
    """Configuración de Google Sheets vía service account."""
    credentials_path: str
    spreadsheet_id: str

    @classmethod
    def from_env(cls) -> "GoogleSheetsConfig":
        return cls(
            credentials_path=os.getenv("GSHEETS_CREDENTIALS_PATH", "./service_account.json"),
            spreadsheet_id=os.getenv("GSHEETS_SPREADSHEET_ID", ""),
        )

    def load_credentials(self) -> dict[str, Any]:
        global _GSHEETS_CREDS_CACHE
        if _GSHEETS_CREDS_CACHE is None:
            path = Path(self.credentials_path)
            if not path.exists():
                raise FileNotFoundError(
                    f"No se encuentra {path}. Creá un service account en "
                    f"https://console.cloud.google.com/apis/credentials y "
                    f"descargá el JSON acá."
                )
            with open(path) as f:
                _GSHEETS_CREDS_CACHE = json.load(f)
        return _GSHEETS_CREDS_CACHE


@dataclass
class EmailConfig:
    """Configuración SMTP para notificaciones."""
    host: str
    port: int
    user: str
    password: str
    notify_to: str

    @classmethod
    def from_env(cls) -> "EmailConfig":
        return cls(
            host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.getenv("SMTP_PORT", "587")),
            user=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            notify_to=os.getenv("NOTIFY_EMAIL", "tejada.ca23@gmail.com"),
        )


@dataclass
class AppConfig:
    """Configuración general de la aplicación."""
    wc: WooCommerceConfig
    gsheets: GoogleSheetsConfig
    email: EmailConfig
    interval_minutes: int = 2

    @classmethod
    def load(cls) -> "AppConfig":
        return cls(
            wc=WooCommerceConfig.from_env(),
            gsheets=GoogleSheetsConfig.from_env(),
            email=EmailConfig.from_env(),
            interval_minutes=int(os.getenv("INTERVAL_MINUTES", "5")),
        )
