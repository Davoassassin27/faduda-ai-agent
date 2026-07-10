"""
field_mapper.py — Mapeo inteligente de campos formulario ↔ columnas sheets.

Arquitectura RAG (Retrieval-Augmented Generation) aplicada a formularios:

  [R] Retrieve:
      - Extrae preguntas/campos del formulario web (HTML o snapshot)
      - Recupera columnas disponibles del Google Sheets
  [A] Augment:
      - Gemini recibe contexto: "campo del form → columna de sheet"
      - Prompt enriquecido con tipos de datos, ejemplos, valores disponibles
  [G] Generate:
      - Gemini genera un mapeo campo→columna (JSON estructurado)
      - El agente usa ese mapeo para llenar cada registro

Esto permite:
  - Formularios dinámicos (cambian sin aviso)
  - Múltiples hojas con diferentes esquemas
  - Campos opcionales vs requeridos (Gemini los detecta)
  - Validación semántica antes del envío
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import google.generativeai as genai

from config import GeminiConfig
from sheet_reader import SheetReader

logger = logging.getLogger("agent.mapper")


class FieldMapper:
    """
    Mapea campos de formulario web a columnas de Google Sheets
    usando Gemini como motor de razonamiento semántico (RAG).
    """

    def __init__(
        self,
        gemini_cfg: GeminiConfig,
        sheet_reader: SheetReader,
    ) -> None:
        genai.configure(api_key=gemini_cfg.api_key)
        self._model = genai.GenerativeModel(
            gemini_cfg.model,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 4096,
            },
        )
        self._sheet_reader = sheet_reader

    # ------------------------------------------------------------------
    # [R] Retrieve: extraer campos del formulario + columnas de sheets
    # ------------------------------------------------------------------

    def extract_form_fields_from_html(self, html_snippet: str) -> list[str]:
        """
        Extrae etiquetas de campos desde HTML del formulario.
        Simple parsing por regex (no necesita DOM completo).
        """
        fields = []

        # Google Forms: labels dentro de divs con role="heading" o aria-label
        patterns = [
            r'<div[^>]*role="heading"[^>]*aria-level="[12]"[^>]*>([^<]+)</div>',
            r'<span[^>]*class="[^"]*M7eMe[^"]*"[^>]*>([^<]+)</span>',
            r'<div[^>]*role="listitem"[^>]*>([^<]+)</div>',
            r'aria-label="([^"]+)"',
            r'placeholder="([^"]+)"',
            r'<label[^>]*>([^<]+)</label>',
        ]

        for pat in patterns:
            matches = re.findall(pat, html_snippet, re.IGNORECASE | re.DOTALL)
            for m in matches:
                clean = re.sub(r'\s+', ' ', m).strip()
                if clean and len(clean) > 3 and clean not in fields:
                    fields.append(clean)

        # Filtrar duplicados y campos genéricos
        skip_words = ["enviar", "submit", "siguiente", "next", "back", "atrás"]
        fields = [
            f for f in fields
            if not any(s in f.lower() for s in skip_words)
        ]

        return fields

    # ------------------------------------------------------------------
    # [A] Augment + [G] Generate: Gemini mapea campos → columnas
    # ------------------------------------------------------------------

    def generate_mapping(
        self,
        form_fields: list[str],
        sheet_headers: list[str],
        sheet_name: str | None = None,
        sample_data: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Gemini analiza los campos del formulario y las columnas del sheet
        y genera un mapeo JSON.

        Retorna lista de:
          {
            "form_field": str,           # texto del campo en el formulario
            "sheet_column": str | None,  # columna que le corresponde
            "confidence": float,         # 0.0 - 1.0
            "reasoning": str,            # por qué eligió esa columna
          }
        """
        prompt = self._build_mapping_prompt(
            form_fields, sheet_headers, sheet_name, sample_data,
        )

        logger.info("Generando mapeo de campos via Gemini...")
        response = self._model.generate_content(
            prompt,
            request_options={"timeout": 30},
        )

        return self._parse_mapping_response(response.text, form_fields)

    def _build_mapping_prompt(
        self,
        form_fields: list[str],
        sheet_headers: list[str],
        sheet_name: str | None,
        sample_data: list[dict[str, Any]] | None,
    ) -> str:
        """Construye prompt con contexto RAG para Gemini."""
        prompt = f"""Eres un sistema de mapeo semántico de formularios web.
Tu tarea es asociar cada CAMPO de un formulario web con la COLUMNA correcta
de una hoja de cálculo de Google Sheets.

## Instrucciones
- Analiza el significado semántico de cada campo del formulario
- Encuentra la columna de la hoja que mejor coincida
- Si no hay coincidencia, marca sheet_column como null
- No inventes columnas que no existan en la lista

## Campos del formulario ({len(form_fields)}):
{json.dumps(form_fields, indent=2, ensure_ascii=False)}

## Columnas disponibles en la hoja de cálculo ({len(sheet_headers)}):
{json.dumps(sheet_headers, indent=2, ensure_ascii=False)}
"""
        if sheet_name:
            prompt += f"\n## Nombre de la hoja: {sheet_name}\n"
        if sample_data:
            prompt += f"\n## Datos de muestra (primeras filas):\n{json.dumps(sample_data, indent=2, ensure_ascii=False)}\n"

        prompt += """
## Formato de respuesta (JSON exacto, sin markdown):
{
  "mappings": [
    {
      "form_field": "texto exacto del campo",
      "sheet_column": "nombre de columna o null",
      "confidence": 0.95,
      "reasoning": "explica breve la coincidencia semántica"
    }
  ]
}

Responde SOLO con el JSON, sin texto adicional.
"""
        return prompt

    def _parse_mapping_response(
        self,
        text: str,
        expected_fields: list[str],
    ) -> list[dict[str, Any]]:
        """Parsea la respuesta JSON de Gemini."""
        # Extraer JSON del texto (puede venir con markdown ```json ... ```)
        json_match = re.search(r'```(?:json)?\s*\n?({.*?})\s*\n?```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        else:
            # Buscar { ... } directo
            json_match = re.search(r'({.*"mappings".*})', text, re.DOTALL)
            if json_match:
                text = json_match.group(1)

        try:
            data = json.loads(text)
            mappings = data.get("mappings", [])
        except json.JSONDecodeError:
            logger.warning("Gemini no devolvió JSON válido. Usando fallback.")
            mappings = [
                {"form_field": f, "sheet_column": None, "confidence": 0.0,
                 "reasoning": "Fallback: parse error"}
                for f in expected_fields
            ]

        return mappings

    # ------------------------------------------------------------------
    # API simplificada
    # ------------------------------------------------------------------

    def map_and_prepare(
        self,
        form_html: str,
        sheet_title: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
    Flujo completo RAG:
      1. Extrae campos del HTML del formulario
      2. Lee headers del sheet
      3. Gemini mapea campos → columnas
      4. Retorna (mappings, registros_del_sheet)

    Returns:
      mappings: [{form_field, sheet_column, confidence, reasoning}]
      records: datos del sheet listos para llenar
"""
        form_fields = self.extract_form_fields_from_html(form_html)
        headers = self._sheet_reader.get_headers(sheet_title)
        records = self._sheet_reader.get_all_records(sheet_title)

        mappings = self.generate_mapping(
            form_fields=form_fields,
            sheet_headers=headers,
            sheet_name=sheet_title,
            sample_data=records[:2] if records else None,
        )

        return mappings, records
