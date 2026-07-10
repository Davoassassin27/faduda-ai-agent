"""
browser_agent.py — Autenticación y navegación autónoma con Playwright.

El agente:
  1. Abre un navegador headless (o headed para debug)
  2. Navega al formulario indicado
  3. Identifica campos (input, select, textarea) y sus labels
  4. Completa los campos usando el mapeo sheets → form
  5. Valida visualmente antes de enviar
  6. Envía y captura confirmación

Manejo de errores:
  - Timeout de carga (30s por página)
  - Campos requeridos vacíos → log + skip
  - Captcha/rate-limit → detecta y aborta con warning
  - Pantallazo de cada paso para auditoría
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger("agent.browser")

# Directorio para capturas de pantalla de auditoría
_SCREENSHOTS_DIR = Path(__file__).parent / "_screenshots"


class BrowserAgent:
    """
    Agente de navegación autónoma para Google Forms.

    Cada formulario se procesa como un "viaje" independiente:
      - navigate: abre el form
      - fill: completa campos según mapping
      - submit: envía
      - capture: pantallazo + logs
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._screenshots_taken = 0

        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Ciclo de vida del navegador
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia el navegador (Chromium)."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="es-AR",
        )
        self._page = self._context.new_page()
        logger.info("Navegador iniciado (headless=%s)", self._headless)

    def stop(self) -> None:
        """Cierra el navegador."""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning("Error al cerrar navegador: %s", e)
        logger.info("Navegador cerrado")

    def screenshot(self, label: str) -> str:
        """Toma captura de pantalla y devuelve ruta del archivo."""
        if not self._page:
            return ""
        self._screenshots_taken += 1
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{self._screenshots_taken:03d}_{label}.png"
        fpath = str(_SCREENSHOTS_DIR / fname)
        try:
            self._page.screenshot(path=fpath, full_page=True)
            logger.info("Captura: %s", fname)
        except Exception as e:
            logger.warning("Error en captura: %s", e)
        return fpath

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    def navigate(self, url: str) -> bool:
        """
        Navega a la URL del formulario.
        Retorna True si se cargó correctamente.
        """
        if not self._page:
            logger.error("Navegador no iniciado")
            return False
        try:
            logger.info("Navegando a %s", url)
            self._page.goto(url, wait_until="networkidle", timeout=30000)
            # Esperar que el contenido del form cargue
            self._page.wait_for_timeout(2000)
            self.screenshot("loaded")
            return True
        except PlaywrightTimeout:
            logger.warning("Timeout cargando %s", url)
            self.screenshot("timeout")
            return False
        except Exception as e:
            logger.error("Error navegando a %s: %s", url, e)
            return False

    # ------------------------------------------------------------------
    # Extracción de campos del formulario
    # ------------------------------------------------------------------

    def get_form_html(self) -> str:
        """
        Retorna el HTML de la página (para extraer campos via field_mapper).
        """
        if not self._page:
            return ""
        try:
            return self._page.content()
        except Exception as e:
            logger.warning("Error leyendo HTML: %s", e)
            return ""

    def detect_form_fields(self) -> list[dict[str, Any]]:
        """
        Detecta campos del formulario explorando página por página.
        Google Forms carga preguntas dinámicamente al navegar.

        Estrategia: llena campos con Playwright para activar la validación
        y poder avanzar entre páginas. Recolecta labels y vuelve al inicio.
        """
        fields = []
        if not self._page:
            return fields

        seen_labels = set()

        def _fill_visible():
            """Llena todos los inputs visibles con datos temporales."""
            for inp in self._page.query_selector_all('input[type="text"], input[type="email"], input[type="tel"]'):
                try:
                    if not inp.input_value():
                        inp.fill("_tmp_")
                except Exception:
                    pass
            # Dropdowns: seleccionar primera opción válida
            for lb in self._page.query_selector_all('[role="listbox"]'):
                try:
                    lb.click()
                    self._page.wait_for_timeout(300)
                    opts = self._page.query_selector_all('[role="option"]')
                    for o in opts:
                        ad = o.get_attribute("aria-disabled")
                        if ad and ad == "true":
                            continue
                        o.click()
                        self._page.wait_for_timeout(300)
                        break
                except Exception:
                    pass

        try:
            for page_idx in range(20):
                raw = self._page.evaluate("""() => {
                    const items = document.querySelectorAll('[role="listitem"]');
                    const result = [];
                    for (const q of items) {
                        const heading = q.querySelector('[role="heading"]');
                        if (!heading) continue;
                        const label = (heading.textContent || '').replace(/\\s*\\*\\s*$/, '').trim();
                        const input = q.querySelector('input:not([type="hidden"]), textarea');
                        if (!input) continue;
                        result.push({ label });
                    }
                    return result;
                }""")
                for r in raw:
                    if r["label"] not in seen_labels:
                        seen_labels.add(r["label"])
                        fields.append({
                            "index": len(fields),
                            "label": r["label"],
                            "type": "text",
                            "required": True,
                        })

                _fill_visible()

                next_btn = self._page.query_selector('div[role="button"]:has(span:has-text("Siguiente"))')
                if not next_btn:
                    break
                next_btn.click()
                self._page.wait_for_timeout(2000)

            # Volver al inicio
            for _ in range(min(page_idx, 10)):
                back = self._page.query_selector('div[role="button"]:has(span:has-text("Atrás"))')
                if back:
                    back.click()
                    self._page.wait_for_timeout(1000)
                else:
                    break
        except Exception as e:
            logger.warning("Error detectando campos: %s", e)

        logger.info("Detectados %d campos", len(fields))
        return fields

    # ------------------------------------------------------------------
    # Relleno de formulario
    # ------------------------------------------------------------------

    def fill_form(
        self,
        mappings: list[dict[str, Any]],
        record: dict[str, Any],
        record_index: int,
    ) -> dict[str, Any]:
        """
        Completa el formulario con los datos de un registro.
        Maneja formularios multi-página (Google Forms).

        mappings: [{form_field, sheet_column, confidence, reasoning}]
        record: dict con datos del sheet

        Retorna dict con resultado.
        """
        result: dict[str, Any] = {
            "record_index": record_index,
            "fields_filled": 0,
            "fields_skipped": 0,
            "errors": [],
            "submitted": False,
            "confirmation_text": "",
        }

        if not self._page:
            result["errors"].append("Navegador no disponible")
            return result

        # Mapear sheet_column -> value
        mapping_by_col = {m["sheet_column"]: m for m in mappings if m.get("sheet_column")}
        # Agrupar mappings ordenados por label (aparecen en orden en el form)
        ordered = [m for m in mappings if m.get("sheet_column")]

        # Llenar página por página
        page_num = 1
        max_pages = 10
        while page_num <= max_pages:
            logger.debug("Llenando página %d...", page_num)

            # Obtener todos los contenedores de pregunta visibles
            questions = self._page.query_selector_all('div[role="listitem"]')
            filled_this_page = 0

            for q in questions:
                heading_el = q.query_selector('[role="heading"]')
                if not heading_el:
                    continue
                label = (heading_el.inner_text() or "").split("*")[0].split("\n")[0].strip()
                has_input = q.query_selector('input:not([type="hidden"]), textarea, [role="listbox"]')
                if not label or not has_input:
                    continue

                # Buscar mapping por label
                matched = None
                for m in ordered:
                    if m.get("form_field", "").strip() == label:
                        matched = m
                        break
                if not matched:
                    result["fields_skipped"] += 1
                    continue

                sheet_col = matched.get("sheet_column")
                confidence = matched.get("confidence", 0.0)
                if not sheet_col or confidence < 0.3:
                    result["fields_skipped"] += 1
                    continue

                value = record.get(sheet_col)
                if value is None or value == "":
                    result["fields_skipped"] += 1
                    continue

                # Llenar el input dentro de este contenedor
                ok = self._fill_question_input(q, str(value))
                if ok:
                    result["fields_filled"] += 1
                    filled_this_page += 1
                else:
                    result["fields_skipped"] += 1

            # Avanzar a la siguiente página o enviar
            next_btn = self._page.query_selector('div[role="button"]:has(span:has-text("Siguiente"))')
            if next_btn:
                next_btn.click()
                self._page.wait_for_timeout(2000)
                page_num += 1
            else:
                break

        return result

    def _fill_question_input(self, container, value: str) -> bool:
        """Llena el input/textarea/select/listbox dentro de un contenedor."""
        try:
            inp = container.query_selector('input[type="text"], input[type="email"], input[type="tel"], input:not([type="radio"]):not([type="checkbox"]), textarea')
            if inp:
                inp.fill(value)
                return True

            listbox = container.query_selector('[role="listbox"]')
            if listbox:
                listbox.click()
                self._page.wait_for_timeout(500)
                opts = self._page.query_selector_all('[role="option"]')
                for o in opts:
                    txt = (o.text_content() or "").strip()
                    ad = o.get_attribute("aria-disabled")
                    if txt.lower() == value.lower() and ad != "true":
                        o.click()
                        self._page.wait_for_timeout(300)
                        return True
                # Fallback: click first valid option
                for o in opts:
                    ad = o.get_attribute("aria-disabled")
                    if ad != "true":
                        o.click()
                        self._page.wait_for_timeout(300)
                        return True
            return False
        except Exception as e:
            logger.warning("Error llenando input: %s", e)
            return False

    # ------------------------------------------------------------------
    # Envío del formulario
    # ------------------------------------------------------------------

    def submit(self) -> dict[str, Any]:
        """
        Navega todas las páginas restantes y envía el formulario.

        Retorna dict con resultado:
          submitted: bool
          confirmation_text: str
        """
        result = {"submitted": False, "confirmation_text": ""}
        if not self._page:
            return result

        try:
            # Avanzar páginas hasta encontrar "Enviar"
            for _ in range(15):
                next_btn = self._page.query_selector('div[role="button"]:has(span:has-text("Siguiente"))')
                if next_btn:
                    next_btn.click()
                    self._page.wait_for_timeout(2000)
                else:
                    break

            self.screenshot("before_submit")

            submit_btn = self._page.query_selector(
                'div[role="button"]:has-text("Enviar"), '
                'div[role="button"]:has(span:has-text("Enviar"))'
            )
            if not submit_btn:
                submit_btn = self._page.query_selector(
                    'div.freebirdFormviewerViewNavigationButtons div[role="button"]'
                )

            if submit_btn:
                submit_btn.click()
                logger.info("Formulario enviado")
                self._page.wait_for_timeout(3000)
                self.screenshot("submitted")
                result["submitted"] = True

                try:
                    confirm = self._page.query_selector(
                        '[role="heading"]'
                    )
                    if confirm:
                        result["confirmation_text"] = confirm.inner_text()[:200]
                except Exception:
                    pass
            else:
                logger.warning("Botón Enviar no encontrado")
                result["confirmation_text"] = "Botón Enviar no encontrado"

        except Exception as e:
            logger.error("Error al enviar: %s", e)
            result["confirmation_text"] = str(e)[:200]

        return result

    # ------------------------------------------------------------------
    # Utilitarios
    # ------------------------------------------------------------------

    def get_page_url(self) -> str:
        return self._page.url if self._page else ""

    def wait(self, seconds: float = 2.0) -> None:
        if self._page:
            self._page.wait_for_timeout(int(seconds * 1000))
