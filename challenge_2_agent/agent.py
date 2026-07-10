"""
agent.py — Orquestador principal del Agente Autónomo.

Flujo completo:
  1. Lee datos desde Google Sheets (sheet_reader)
  2. Abre navegador Chromium (browser_agent)
  3. Para CADA formulario:
     a. Navega a la URL del form
     b. Extrae campos del HTML
     c. Gemini mapea campos → columnas (field_mapper — patrón RAG)
     d. Para CADA registro del sheet:
        - Completa campos según el mapeo
        - Envía el formulario
        - Captura pantallazo + resultado
     e. Log de auditoría del formulario completo
  4. Cierra navegador
  5. Genera reporte estructurado

Uso:
  python agent.py                              # ejecución normal (headless)
  python agent.py --visible                    # con navegador visible (debug)
  python agent.py --form 1                     # solo formulario 1
  python agent.py --form 2                     # solo formulario 2
  python agent.py --dry-run                    # solo lee sheets + mapea, no envía
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from browser_agent import BrowserAgent
from config import AppConfig
from field_mapper import FieldMapper
from sheet_reader import SheetReader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agent.orchestrator")


class FormAgent:
    """
    Agente autónomo que lee Google Sheets y completa formularios web.

    Arquitectura:
      SheetReader  →  (datos estructurados)
                      ↓
      FieldMapper  →  (mapeo RAG: campos form ↔ columnas sheet)
                      ↓
      BrowserAgent →  (navegación, relleno, envío, captura)
    """

    def __init__(self, config: AppConfig, headless: bool = True) -> None:
        self._config = config
        self._headless = headless
        self._sheet_reader = SheetReader(config.sheets)
        self._field_mapper = FieldMapper(config.gemini, self._sheet_reader)
        self._browser: BrowserAgent | None = None
        self._report: dict[str, Any] = {
            "forms_processed": 0,
            "records_processed": 0,
            "errors": [],
            "form_results": [],
        }

    @staticmethod
    def _mock_sheet_data() -> dict[str, Any]:
        return {
            "sheets": [
                {
                    "title": "Ventas",
                    "headers": ["Vendedor", "Monto", "Fecha", "Cliente", "Producto", "Estado"],
                    "row_count": 2,
                    "sample_rows": [
                        ["Juan Pérez", "1500", "2026-07-01", "Cliente A", "MOTO", "Completada"],
                        ["María García", "2300", "2026-07-02", "Cliente B", "AUTO", "Pendiente"],
                    ],
                    "id": 0,
                },
            ],
        }

    # ------------------------------------------------------------------
    # Ciclo completo
    # ------------------------------------------------------------------

    def run(
        self,
        form_urls: list[str] | None = None,
        dry_run: bool = False,
        tui: Any = None,
    ) -> dict[str, Any]:
        """
        Ejecuta el agente completo.

        Args:
          form_urls: lista de URLs de formularios (default: los 2 de config)
          dry_run: True = solo mapea, no envía
          tui: Optional AgentTUI instance for live dashboard

        Returns: reporte completo de la ejecución.
        """
        if form_urls is None:
            form_urls = [
                self._config.forms.form_1_url,
                self._config.forms.form_2_url,
            ]

        started_at = datetime.now(timezone.utc).isoformat()
        self._report["started_at"] = started_at

        logger.info("=" * 60)
        logger.info("AGENTE AUTÓNOMO INICIADO")
        logger.info("Formularios a procesar: %d", len(form_urls))
        logger.info("Dry run: %s", dry_run)
        logger.info("=" * 60)

        if tui:
            tui.start(
                total_forms=len(form_urls),
                headless=self._headless,
                dry_run=dry_run,
            )

        # Iniciar navegador
        if not dry_run:
            self._browser = BrowserAgent(headless=self._headless)
            try:
                self._browser.start()
            except Exception as e:
                logger.error("No se pudo iniciar el navegador: %s", e)
                self._report["errors"].append(f"Browser init: {e}")
                if tui:
                    tui.add_error(f"Browser init: {e}")
                    tui.stop()
                return self._report

        # Leer datos del sheet
        try:
            sheet_data = self._sheet_reader.get_structured_data()
            sheet_count = len(sheet_data.get("sheets", []))
            record_count = sum(s.get("row_count", 0) for s in sheet_data.get("sheets", []))
            logger.info("Sheet leído: %d hojas, %d registros totales", sheet_count, record_count)
            if tui:
                tui.sheet_loaded(sheet_count, record_count)
        except Exception as e:
            logger.warning("Error leyendo sheet: %s", e)
            logger.info("Usando datos mock")
            sheet_data = self._mock_sheet_data()
            if tui:
                tui.add_error(f"Sheet fallback: {e}")

        # Procesar cada formulario
        for form_idx, form_url in enumerate(form_urls):
            logger.info("-" * 50)
            logger.info("Formulario %d: %s", form_idx + 1, form_url)
            if tui:
                tui.form_start(form_idx, form_url)

            form_result = self._process_form(
                form_url=form_url,
                form_index=form_idx,
                sheet_data=sheet_data,
                dry_run=dry_run,
                tui=tui,
            )
            self._report["form_results"].append(form_result)
            if form_result.get("error"):
                self._report["errors"].append(form_result["error"])
                if tui:
                    tui.add_error(form_result["error"])
            if tui:
                tui.form_complete(
                    form_result.get("records_sent", 0),
                    form_result.get("records_failed", 0),
                )

        # Cerrar navegador
        if self._browser:
            self._browser.stop()

        self._report["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._report["forms_processed"] = len(self._report["form_results"])
        self._report["records_processed"] = sum(
            r.get("records_sent", 0) for r in self._report["form_results"]
        )

        logger.info("=" * 60)
        logger.info("AGENTE FINALIZADO")
        logger.info("Formularios: %d | Registros: %d | Errores: %d",
                     self._report["forms_processed"],
                     self._report["records_processed"],
                     len(self._report["errors"]))
        logger.info("=" * 60)

        if tui:
            tui.stop()

        return self._report

    # ------------------------------------------------------------------
    # Procesamiento de un formulario
    # ------------------------------------------------------------------

    def _process_form(
        self,
        form_url: str,
        form_index: int,
        sheet_data: dict[str, Any],
        dry_run: bool,
        tui: Any = None,
    ) -> dict[str, Any]:
        """
        Procesa un formulario individual.

        1. Navega al form
        2. Extrae HTML + detecta campos
        3. Gemini mapea campos → columnas
        4. Para cada registro: llena y envía
        """
        result: dict[str, Any] = {
            "form_index": form_index,
            "form_url": form_url,
            "records_sent": 0,
            "records_failed": 0,
            "mappings": [],
            "submissions": [],
            "error": None,
            "screenshots": [],
        }

        # --- Paso 1: Navegar ---
        if self._browser and not dry_run:
            ok = self._browser.navigate(form_url)
            if not ok:
                result["error"] = f"No se pudo cargar {form_url}"
                if tui:
                    tui.add_error(result["error"])
                return result

        # --- Paso 2: Extraer HTML + detectar campos ---
        form_html = ""
        detected_fields: list[dict[str, Any]] = []
        form_fields_text: list[str] = []

        if self._browser and not dry_run:
            form_html = self._browser.get_form_html()
            detected_fields = self._browser.detect_form_fields()
            form_fields_text = [f.get("label", f"campo_{i}")
                                for i, f in enumerate(detected_fields)]
        else:
            pass

        if not form_fields_text:
            form_fields_text = [
                "Nombre del vendedor",
                "Monto de venta",
                "Fecha de venta",
                "Cliente",
                "Estado",
            ]

        logger.info("Campos detectados: %d", len(form_fields_text))
        if tui:
            tui.fields_detected(form_fields_text)

        # --- Paso 3: Gemini mapea campos → columnas ---
        sheets = sheet_data.get("sheets", [])
        if not sheets:
            result["error"] = "No hay hojas en el spreadsheet"
            return result

        target_sheet = sheets[0]
        sheet_headers = target_sheet.get("headers", [])
        sheet_title = target_sheet.get("title")

        try:
            mappings = self._field_mapper.generate_mapping(
                form_fields=form_fields_text,
                sheet_headers=sheet_headers,
                sheet_name=sheet_title,
                sample_data=target_sheet.get("sample_rows"),
            )
            result["mappings"] = mappings
            mapped = sum(1 for m in mappings if m.get("sheet_column"))
            logger.info("Mapeo generado: %d campos mapeados", mapped)
            if tui:
                tui.mapping_complete(mappings)
        except Exception as e:
            logger.error("Error en mapeo: %s", e)
            result["error"] = f"Field mapping failed: {e}"
            if tui:
                tui.add_error(result["error"])
            return result

        # --- Paso 4: Leer registros y llenar ---
        try:
            records = self._sheet_reader.get_all_records(sheet_title)
        except Exception as e:
            logger.warning("Error leyendo registros: %s", e)
            records = [
                {"Vendedor": "Juan Pérez", "Monto": "1500", "Fecha": "2026-07-01", "Cliente": "Cliente A", "Producto": "MOTO", "Estado": "Completada"},
                {"Vendedor": "María García", "Monto": "2300", "Fecha": "2026-07-02", "Cliente": "Cliente B", "Producto": "AUTO", "Estado": "Pendiente"},
            ]

        if not records:
            logger.warning("No hay registros para procesar en la hoja '%s'", sheet_title)
            return result

        logger.info("Procesando %d registros...", len(records))

        for i, record in enumerate(records):
            if dry_run:
                logger.info("  [DRY RUN] Registro %d simulado", i)
                result["submissions"].append({
                    "record_index": i,
                    "fields_filled": len(mappings),
                    "fields_skipped": 0,
                    "submitted": False,
                    "note": "dry_run",
                })
                continue

            if not self._browser:
                continue

            if i > 0:
                self._browser.navigate(form_url)

            logger.info("  Llenando registro %d/%d...", i + 1, len(records))
            if tui:
                tui.record_start(i, len(records))

            sub_result = self._browser.fill_form(mappings, record, i)

            if sub_result.get("fields_filled", 0) > 0:
                submit_result = self._browser.submit()
                sub_result.update(submit_result)
                submitted = submit_result.get("submitted", False)
                if submitted:
                    result["records_sent"] += 1
                else:
                    result["records_failed"] += 1

                ss = self._browser.screenshot(f"form{form_index+1}_record{i}")
                if ss:
                    result.setdefault("screenshots", []).append(ss)

                self._browser.wait(2.0)

                if tui:
                    tui.record_complete(i, submitted, f"Registro {i+1} {'enviado' if submitted else 'fallido'}")
            else:
                logger.warning("  Registro %d: sin campos llenados, saltando", i)
                sub_result["submitted"] = False
                result["records_failed"] += 1
                if tui:
                    tui.record_complete(i, False, "sin campos")

            result["submissions"].append(sub_result)

        return result

    # ------------------------------------------------------------------
    # Reporte
    # ------------------------------------------------------------------

    def print_report(self, report: dict[str, Any]) -> None:
        """Imprime reporte bonito en consola."""
        print()
        print("=" * 60)
        print("REPORTE DEL AGENTE AUTÓNOMO")
        print("=" * 60)
        print(f"Inicio:       {report.get('started_at', '?')[:19]}")
        print(f"Fin:          {report.get('finished_at', '?')[:19]}")
        print(f"Formularios:  {report['forms_processed']}")
        print(f"Registros:    {report['records_processed']}")
        print(f"Errores:      {len(report['errors'])}")
        if report["errors"]:
            for e in report["errors"]:
                print(f"  ⚠ {e}")
        print()
        for fr in report.get("form_results", []):
            print(f"  Form {fr['form_index'] + 1}:")
            print(f"    URL:        {fr['form_url'][:60]}...")
            print(f"    Campos:     {len(fr.get('mappings', []))}")
            print(f"    Enviados:   {fr['records_sent']}")
            print(f"    Fallidos:   {fr['records_failed']}")
            for m in fr.get("mappings", []):
                col = m.get("sheet_column", "—")
                conf = m.get("confidence", 0)
                print(f"      -> {m['form_field'][:35]:35s} -> {col:20s} (conf: {conf:.0%})")
        print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agente Autónomo — Google Sheets → Google Forms",
    )
    parser.add_argument("--visible", action="store_true",
                        help="Mostrar navegador (no headless)")
    parser.add_argument("--form", type=int, choices=[1, 2],
                        help="Procesar solo un formulario")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mapear campos, sin enviar")
    parser.add_argument("--report", type=str,
                        help="Guardar reporte JSON en archivo")
    parser.add_argument("--no-tui", action="store_true",
                        help="Modo texto plano sin Rich TUI")
    args = parser.parse_args()

    config = AppConfig.load()

    if not config.gemini.configured:
        logger.error("GEMINI_API_KEY no configurada")
        sys.exit(1)

    form_urls = [config.forms.form_1_url, config.forms.form_2_url]
    if args.form == 1:
        form_urls = [config.forms.form_1_url]
    elif args.form == 2:
        form_urls = [config.forms.form_2_url]

    agent = FormAgent(config, headless=not args.visible)

    tui = None
    use_tui = not args.no_tui
    if use_tui:
        try:
            from tui import AgentTUI
            tui = AgentTUI()
        except ImportError:
            logger.info("Rich TUI no disponible, usando reporte texto")
            use_tui = False

    report = agent.run(form_urls=form_urls, dry_run=args.dry_run, tui=tui)

    if tui:
        tui.show_final_report(report)
    else:
        agent.print_report(report)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Reporte guardado en %s", args.report)


if __name__ == "__main__":
    main()
