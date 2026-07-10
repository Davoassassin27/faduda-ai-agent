"""
sheet_reader.py — Lector de Google Sheets con detección de esquema.

Extrae datos de todas las hojas del spreadsheet y las expone como
diccionarios planos para que el agente las procese.

El agente usa estos datos combinados con la descripción semántica
de los campos del formulario (vía Gemini) para:
  1. Identificar qué columna corresponde a cada campo del form
  2. Extraer el valor adecuado para cada registro
  3. Validar tipos antes del envío
"""
from __future__ import annotations

import logging
from typing import Any

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from config import SheetsConfig

logger = logging.getLogger("agent.sheets")


class SheetReader:
    """Lee y expone datos de Google Sheets con metadatos de esquema."""

    def __init__(self, cfg: SheetsConfig) -> None:
        self._cfg = cfg
        self._client: gspread.Client | None = None

    def _auth(self) -> gspread.Client:
        if self._client is None:
            creds_dict = self._cfg.load_credentials()
            scope = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self._client = gspread.authorize(creds)
        return self._client

    def get_worksheets_metadata(self) -> list[dict[str, Any]]:
        """
        Retorna metadatos de todas las hojas del spreadsheet:
          [{ "title": str, "rows": int, "cols": int, "headers": [str] }]
        """
        client = self._auth()
        sh = client.open_by_key(self._cfg.spreadsheet_id)
        result = []
        for ws in sh.worksheets():
            try:
                headers = ws.row_values(1)
                result.append({
                    "title": ws.title,
                    "rows": ws.row_count,
                    "cols": ws.col_count,
                    "headers": headers,
                })
            except Exception as e:
                logger.warning("Hoja '%s' no legible: %s", ws.title, e)
        return result

    def get_all_records(self, sheet_title: str | None = None) -> list[dict[str, Any]]:
        """
        Retorna todos los registros de una hoja como lista de dicts.

        Si sheet_title es None, lee la primera hoja del spreadsheet.
        """
        client = self._auth()
        sh = client.open_by_key(self._cfg.spreadsheet_id)
        if sheet_title:
            ws = sh.worksheet(sheet_title)
        else:
            ws = sh.sheet1
        return ws.get_all_records()

    def get_row_count(self, sheet_title: str | None = None) -> int:
        """Cantidad de filas de datos (sin cabecera)."""
        try:
            records = self.get_all_records(sheet_title)
            return len(records)
        except Exception:
            return 0

    def get_headers(self, sheet_title: str | None = None) -> list[str]:
        """Nombres de columnas de la hoja."""
        client = self._auth()
        sh = client.open_by_key(self._cfg.spreadsheet_id)
        if sheet_title:
            ws = sh.worksheet(sheet_title)
        else:
            ws = sh.sheet1
        return ws.row_values(1)

    def get_structured_data(self) -> dict[str, Any]:
        """
        Retorna estructura completa del spreadsheet:
          {
            "sheets": [
              {
                "title": str,
                "headers": [str],
                "row_count": int,
                "sample_rows": [dict]  # primeras 2 filas como muestra
              }
            ]
          }
        """
        client = self._auth()
        sh = client.open_by_key(self._cfg.spreadsheet_id)
        result = {"spreadsheet_id": self._cfg.spreadsheet_id, "sheets": []}
        for ws in sh.worksheets():
            try:
                headers = ws.row_values(1)
                all_records = ws.get_all_records()
                sample = all_records[:2] if all_records else []
                result["sheets"].append({
                    "title": ws.title,
                    "headers": headers,
                    "row_count": len(all_records),
                    "col_count": len(headers),
                    "sample_rows": sample,
                })
            except Exception as e:
                logger.warning("Saltando hoja '%s': %s", ws.title, e)
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from config import AppConfig
    app = AppConfig.load()
    reader = SheetReader(app.sheets)
    meta = reader.get_structured_data()
    import json
    print(json.dumps(meta, indent=2, default=str, ensure_ascii=False))
