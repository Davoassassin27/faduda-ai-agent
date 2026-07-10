"""
sheets_sync.py — Sincronización de productos a Google Sheets.

Toma los datos normalizados del pipeline dlt y los escribe en
Google Sheets manteniendo:
  - Una cabecera fija con nombres de columna
  - Una fila por producto con id, nombre, precio, URL de imagen
  - Actualización completa (truncate + rewrite) por simplicidad transaccional
"""
from __future__ import annotations

import logging
from typing import Any

import gspread
from gspread import Worksheet
from oauth2client.service_account import ServiceAccountCredentials

import config as cfg

logger = logging.getLogger("wc.sheets_sync")

# Columnas del Google Sheet (ordenadas como aparecen en la hoja)
SHEET_HEADERS = [
    "id", "name", "price", "regular_price", "sale_price",
    "image_url", "sku", "stock_status", "category_name",
    "permalink", "date_created", "date_modified", "ingested_at",
]

SHEET_NAME = "Productos WooCommerce"


def _get_client(gs_cfg: cfg.GoogleSheetsConfig) -> gspread.Client:
    """Autentica y devuelve cliente de Google Sheets."""
    creds_dict = gs_cfg.load_credentials()
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)


def _ensure_sheet(client: gspread.Client, spreadsheet_id: str) -> Worksheet:
    """Obtiene o crea la hoja de trabajo 'Productos WooCommerce'."""
    try:
        sh = client.open_by_key(spreadsheet_id)
    except gspread.SpreadsheetNotFound:
        sh = client.create("FADUA — Productos WooCommerce")
        # compartir con el evaluador
        # sh.share("tejada.ca23@gmail.com", perm_type="user", role="writer")

    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=20)
        ws.append_row(SHEET_HEADERS)

    return ws


def sync_to_sheets(
    products: list[dict[str, Any]],
    gs_cfg: cfg.GoogleSheetsConfig,
) -> int:
    """
    Escribe productos en Google Sheets (truncate + rewrite).

    Retorna la cantidad de filas escritas (sin contar cabecera).
    """
    client = _get_client(gs_cfg)
    ws = _ensure_sheet(client, gs_cfg.spreadsheet_id)

    # Preparar filas de datos
    rows = []
    for p in products:
        row = []
        for h in SHEET_HEADERS:
            val = p.get(h, "")
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            row.append(val)
        rows.append(row)

    # Truncate + rewrite (transacción atómica en sheets)
    ws.clear()
    ws.update([SHEET_HEADERS] + rows, value_input_option="USER_ENTERED")

    # Ajustar ancho de columnas para legibilidad
    try:
        ws.format("A1:M1", {
            "backgroundColor": {"red": 0.15, "green": 0.39, "blue": 0.92},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1},
                           "bold": True, "fontSize": 11},
        })
        ws.format(f"A2:M{len(rows) + 1}", {"textFormat": {"fontSize": 10}})
        ws.set_column_widths([
            {"range": {"startColumnIndex": 0, "endColumnIndex": 1}, "width": 60},
            {"range": {"startColumnIndex": 1, "endColumnIndex": 2}, "width": 300},
            {"range": {"startColumnIndex": 2, "endColumnIndex": 5}, "width": 90},
            {"range": {"startColumnIndex": 5, "endColumnIndex": 6}, "width": 250},
        ])
    except Exception:
        pass  # formato no crítico

    logger.info("Sincronizados %d productos a Google Sheets", len(rows))
    return len(rows)


def get_product_count(gs_cfg: cfg.GoogleSheetsConfig) -> int:
    """Lee la cantidad actual de productos en la hoja (para diff)."""
    try:
        client = _get_client(gs_cfg)
        sh = client.open_by_key(gs_cfg.spreadsheet_id)
        ws = sh.worksheet(SHEET_NAME)
        records = ws.get_all_records()
        return len(records)
    except Exception:
        return 0
