"""
scheduler.py — Orquestador principal de la sincronización WooCommerce → Sheets.

Ejecuta cada 5 minutos:
  1. Pipeline dlt: extrae productos desde WooCommerce REST API
  2. Google Sheets: escribe los datos normalizados
  3. Email: notifica resumen con tabla de productos

Modo de uso:
  python scheduler.py              # ejecución única (con TUI)
  python scheduler.py --daemon     # loop cada 5 min
  python scheduler.py --cron       # imprime línea para crontab
  python scheduler.py --no-tui     # modo texto plano (log only)

Arquitectura:
  dlt (extracción + inferencia esquema + merge incremental)
    → DuckDB (capa staging, datos planos)
      → gspread (Google Sheets, presentación)
        → SMTP (notificación por email)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as cfg
import dlt_pipeline
import email_notifier
import sheets_sync

# Configurar logging temprano
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wc.scheduler")

# Persistencia del conteo anterior (archivo simple, evitar estado en BD)
_COUNT_FILE = Path(__file__).parent / ".last_count"


def _read_last_count() -> int:
    try:
        return int(_COUNT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_last_count(count: int) -> None:
    _COUNT_FILE.write_text(str(count))


def run_once(app: cfg.AppConfig, tui: Any = None) -> dict[str, Any]:
    """
    Ejecuta un ciclo completo de sincronización.

    1. WooCommerce → dlt pipeline → DuckDB
    2. DuckDB → gspread → Google Sheets
    3. SMTP → email de resumen

    Retorna: dict con métricas de la ejecución.
    """
    start = time.time()
    products: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "status": "ok",
        "products_count": 0,
        "previous_count": 0,
        "email_sent": False,
        "elapsed_s": 0.0,
        "errors": [],
    }

    # --- Paso 1: dlt pipeline ---
    if tui:
        tui.step_running("dlt", "WooCommerce → DuckDB")
    try:
        load_info = dlt_pipeline.run_pipeline(app.wc)
        if tui:
            tui.step_ok("dlt", "Pipeline completado")
    except Exception as e:
        logger.exception("Fallo en pipeline dlt")
        result["errors"].append(f"dlt: {e}")
        result["status"] = "degraded"
        if tui:
            tui.step_fail("dlt", str(e)[:60])

    # --- Paso 2: Google Sheets ---
    if tui:
        tui.step_running("sheets", "DuckDB → Google Sheets")
    try:
        products = dlt_pipeline.get_loaded_products()
        if not products:
            logger.warning("No hay productos para sincronizar")
            result["errors"].append("Sin productos en DuckDB")
            if tui:
                tui.step_fail("sheets", "0 productos en DuckDB")
        else:
            rows = sheets_sync.sync_to_sheets(products, app.gsheets)
            result["products_count"] = rows
            if tui:
                tui.step_ok("sheets", f"{rows} filas escritas")
    except Exception as e:
        logger.exception("Fallo en Google Sheets")
        result["errors"].append(f"sheets: {e}")
        result["status"] = "degraded"
        if tui:
            tui.step_fail("sheets", str(e)[:60])

    # --- Paso 3: Email ---
    if tui:
        tui.step_running("email", "SMTP notification")
    try:
        prev = _read_last_count()
        result["previous_count"] = prev
        sent = email_notifier.send_notification(
            products or [],
            prev,
            time.time() - start,
            app.email,
        )
        result["email_sent"] = sent
        _write_last_count(result["products_count"])
        if tui:
            if sent:
                tui.step_ok("email", f"Enviado a {app.email.notify_to}")
            else:
                tui.step_skip("email", "SMTP no configurado")
    except Exception as e:
        logger.exception("Fallo en notificación email")
        result["errors"].append(f"email: {e}")
        if tui:
            tui.step_fail("email", str(e)[:60])

    result["elapsed_s"] = round(time.time() - start, 2)
    if tui:
        tui.set_product_count(result["products_count"], result["previous_count"])
    return result


def run_daemon(app: cfg.AppConfig, use_tui: bool = True) -> None:
    """Loop infinito ejecutando cada INTERVAL_MINUTES minutos."""
    interval = app.interval_minutes * 60
    cycle = 0

    if use_tui:
        from tui import PipelineTUI
        tui = PipelineTUI()

    while True:
        cycle += 1
        if use_tui:
            tui.start_cycle(cycle, mode="daemon")

        try:
            result = run_once(app, tui if use_tui else None)
            if result["errors"]:
                logger.warning("Errores en ciclo: %s", "; ".join(result["errors"]))
        except Exception as e:
            logger.exception("Error crítico en ciclo")

        if use_tui:
            tui.end_cycle(result)
            tui.countdown(interval)
        else:
            logger.info("Esperando %d segundos hasta próximo ciclo...", interval)
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FADUA — Sincronización WooCommerce → Google Sheets",
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Ejecutar en loop infinito cada 5 minutos",
    )
    parser.add_argument(
        "--cron", action="store_true",
        help="Imprimir línea de crontab para copiar/pegar",
    )
    parser.add_argument(
        "--no-tui", action="store_true",
        help="Modo texto plano sin Rich TUI",
    )
    args = parser.parse_args()

    if args.cron:
        script = Path(__file__).resolve()
        print(f"# Agregar al crontab (crontab -e):")
        print(f"*/5 * * * * cd {script.parent} && {sys.executable} {script} >> scheduler.log 2>&1")
        return

    app = cfg.AppConfig.load()
    use_tui = not args.no_tui and not args.cron

    if args.daemon:
        run_daemon(app, use_tui=use_tui)
    else:
        if use_tui:
            try:
                from tui import PipelineTUI
                tui = PipelineTUI()
                tui.start_cycle()
                result = run_once(app, tui)
                tui.end_cycle(result)
            except ImportError:
                logger.warning("Rich TUI no disponible, usando modo texto")
                result = run_once(app)
        else:
            result = run_once(app)

        if not use_tui:
            print(f"\nResumen:")
            print(f"  Productos:       {result['products_count']}")
            print(f"  Email enviado:   {result['email_sent']}")
            print(f"  Tiempo:          {result['elapsed_s']}s")
            print(f"  Errores:         {'; '.join(result['errors']) if result['errors'] else 'ninguno'}")


if __name__ == "__main__":
    main()
