"""
llm.py - Orquestacion del LLM (Google Gemini) con function calling.

Mejoras v2:
  * Prompt mas corto para reducir tokens/latencia/cuota.
  * Tool unico forecast_metrics(metrics: list) para hacer pronosticos batch
    de varias metricas en una sola llamada al LLM (no 2 viajes).
  * Backoff respeta retry_after devuelto por Gemini en 429.
  * Cache de forecasts via predictor.forecast_metrics (TTL 5 min).
  * Respuesta estructurada: answer (markdown) + sql[] + db_results[] +
    forecasts[] + charts[] + tool_calls + elapsed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

import db
import predictor

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

logger = logging.getLogger(__name__)
genai.configure(api_key=GEMINI_API_KEY)

# Estilo de respuesta en markdown (RESPONSE_STYLE.md embebido como prompt).
RESPONSE_STYLE = """\
Respondes SIEMPRE en español con MARKDOWN según este estilo:

ESTRUCTURA:
1) Titulo `##` describiendo el tipo de analisis.
2) Una frase-ejecutiva con la cifra principal en **negrita**.
3) Detalle en lista o tabla markdown (tabla si hay 2-8 filas comparables).
4) (Opcional) Footer con observacion o disclaimer.

CIFRAS:
- USD con separador de miles: `USD 254.000`. Cantidades enteras: `2.783`.
- Porcentajes 1 decimal con coma: `5,4 %%`. Cifras >6 digitos abreviar (`51,5 mil`, `1,2 M`).
- NUNCA inventes numeros.

PARA PREDICCIONES:
```
## 🔮 Proyeccion — <Mes Año>
Para el proximo mes se proyectan **157 ventas** y **2.852 leads**.
| Metrica | Punto | IC 80%% | IC 95%% |
|---------|------:|--------:|--------:|
| Ventas | 157 | 54 – 259 | 0 – 314 |
```
Mención: intervalos reflejan incertidumbre; el backend grafica historico+forecast.

TONO: profesional, ejecutivo, conciso. 1 emoji por respuesta max. No muestres SQL crudo.
Si la pregunta esta fuera de alcance (metricas de campanas), explicalo brevemente.
"""

SYSTEM_PROMPT_TEMPLATE = """\
Sos un **Especialista en Business Intelligence (BI) y Cientifico de Datos** \
que trabaja para una concesionaria de vehiculos. Combinas el rigor estadistico \
de un data scientist con el foco de negocio de un analista BI. Hablas como un \
consultor senior: claro, conciso, orientado a accion.

# Esquema de la base de datos MySQL
{schema}

REGLAS CLAVE:
1. Para datos REALES usa la herramienta `execute_sql` generando SELECT/WITH \
valido. Una sola consulta bien construida es mejor que varias.
2. La columna `fecha` es DATE con datos DIARIOS hasta el ultimo dia con \
informacion. Para "al dia" usa: \
WHERE fecha = (SELECT MAX(fecha) FROM metricas_campanas_ventas). NO uses CURDATE().
3. Para PROYECCIONES futuras (palabras: proyectad/pronostic/predec/futur/proxim) \
usa UNA sola llamada a `forecast_metrics` con la lista de metricas \
(podes pedir 'ventas', 'leads', 'ingresos'). Nunca invoques la herramienta mas \
de una vez por respuesta.
4. Para analisis por mes: agrupa con DATE_FORMAT(fecha, '%%Y-%%m').
5. Si necesitas comparar leads vs ventas, calcula ratio ventas/leads en el \
mismo SELECT. Aporta interpretacion: ¿que significa el ratio? ¿es buen nivel?
6. Respondes en MARKDOWN siguiendo el estilo de abajo.

DUREZA ANALITICA:
- Aporta contexto comparativo (vs mes anterior, vs promedio) cuando aplique.
- Senala anomalias o tendencias (ej: "crecio 12%% m/m").
- Para predicciones, menciona factores que podrian romper el modelo.

{style}
"""


# ---------------------------------------------------------------------------
# Tools expuestas al LLM
# ---------------------------------------------------------------------------
def execute_sql(sql_query: str) -> str:
    """Ejecuta SELECT/WITH de SOLO LECTURA en MySQL. Devuelve filas como texto tabulado."""
    try:
        result = db.run_readonly_query(sql_query)
        cols = result["columns"]
        rows = result["rows"]
        if not rows:
            return "La consulta no devolvio filas."
        lines = ["\t".join(str(c) for c in cols)]
        for r in rows[: db.MAX_ROWS]:
            lines.append("\t".join(str(r.get(c, "")) for c in cols))
        return "\n".join(lines)
    except db.SQLValidationError as e:
        return f"ERROR_VALIDACION: {e}"
    except RuntimeError as e:
        return f"ERROR_BD: {e}"
    except Exception as e:
        logger.exception("execute_sql inesperado")
        return f"ERROR: {e}"


def forecast_metrics(metrics, periods: int = 1) -> str:
    """
    Pronostica VARIAS metricas en una sola llamada (mas eficiente que
    invocar forecast_metric por separado). Usalo SIEMPRE que el usuario pida
    proyectar mas de una metrica o 'leads y ventas'.

    Args:
        metrics: lista de metricas a pronosticar. Validas: 'ventas', 'leads',
                 'ingresos'. Acepta tambien un string separado por comas.
        periods: cantidad de meses a proyectar (default 1, max 12).

    Devuelve texto con valor punto + intervalos 80%%/95%% por metrica.
    """
    try:
        # Normalizar metrics: puede venir como list, str, o numero
        if metrics is None:
            return "ERROR: metricas vacias. Pasar 'ventas', 'leads' o 'ingresos'."
        if isinstance(metrics, str):
            metrics = [m.strip() for m in metrics.split(",") if m.strip()]
        if isinstance(metrics, (int, float)):
            metrics = [str(metrics)]
        if not isinstance(metrics, (list, tuple)):
            return f"ERROR: metrics debe ser lista. Recibi: {type(metrics).__name__}"
        metrics = [str(m).strip().lower().replace(" ", "_") for m in metrics if m]
        if not metrics:
            return "ERROR: no hay metricas validas."
        res = predictor.forecast_metrics(list(metrics), periods)
        out = []
        for f in res["forecasts"]:
            if "error" in f:
                out.append(f"[{f['metric']}] ERROR: {f['error']}")
                continue
            p = f["forecast"]
            l80, u80 = f["lower_80"], f["upper_80"]
            l95, u95 = f["lower_95"], f["upper_95"]
            out.append(
                f"Metrica: {f['metric']}\n"
                f"Meses proyectados: {', '.join(f['projected_months'])}\n"
                f"Historico ultimo mes ({f['last_historical_month']}): "
                f"{f['last_historical_value']:.2f}\n"
                f"Punto: {', '.join(f'{v:.2f}' for v in p)}\n"
                f"IC80: {', '.join(f'[{l:.2f}..{u:.2f}]' for l,u in zip(l80,u80))}\n"
                f"IC95: {', '.join(f'[{l:.2f}..{u:.2f}]' for l,u in zip(l95,u95))}\n"
                f"Metodo: {f['method']} (n={f['n_history']})\n"
                f"Disclaimer: {f['disclaimer']}"
            )
        out.append(f"\nDisclaimer general: {res['disclaimer']}")
        return "\n\n".join(out)
    except Exception as e:
        logger.exception("forecast_metrics inesperado")
        return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Sesion de chat
# ---------------------------------------------------------------------------
_model: genai.GenerativeModel | None = None
_schema_injected: str | None = None


GEN_CFG = {
    "max_output_tokens": 2048,  # suficiente para respuestas combinadas (BI + proyeccion)
    "temperature": 0.3,
    "top_p": 0.9,
}


def _get_model() -> genai.GenerativeModel:
    global _model, _schema_injected
    schema = db.get_schema_description()
    if _model is None or _schema_injected != schema:
        prompt = SYSTEM_PROMPT_TEMPLATE.format(schema=schema, style=RESPONSE_STYLE)
        _model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            tools=[execute_sql, forecast_metrics],
            system_instruction=prompt,
            generation_config=GEN_CFG,
        )
        _schema_injected = schema
    return _model


# Predicate para parsear RetryAfter de un mensaje 429
_RETRY_AFTER_RE = re.compile(r"retry_delay\s*\{\s*seconds:\s*(\d+)\s*\}", re.IGNORECASE)


def _extract_retry_after(exc: Exception) -> int:
    txt = str(exc)
    m = _RETRY_AFTER_RE.search(txt)
    if m:
        return max(1, int(m.group(1)))
    if "RESOURCE" in txt.upper() or "429" in txt:
        return 30
    return 0


def chat(question: str) -> dict[str, Any]:
    """
    Procesa una pregunta y devuelve respuesta ESTRUCTURADA:
      {
        "answer": "markdown con la redaccion final",
        "sql": [str, ...],            # SQLs ejecutados
        "db_results": [{columns, rows}],
        "forecasts": [{...}],         # raw forecasts (para graficar)
        "tool_calls": [{name, args}],
        "elapsed_s": float,
      }
    Raises RuntimeError si Gemini no responde tras 3 intentos con backoff.
    """
    start = time.time()
    model = _get_model()
    session = model.start_chat(enable_automatic_function_calling=True)

    tool_calls: list[dict[str, Any]] = []
    sql_runs: list[str] = []
    db_results: list[dict[str, Any]] = []
    forecasts: list[dict[str, Any]] = []
    last_exc: Exception | None = None

    for attempt in range(3):
        try:
            response = session.send_message(question)
            # Captura finish_reason para detectar truncamiento por MAX_TOKENS
            finish_reason = None
            candidates = getattr(response, "candidates", []) or []
            if candidates:
                finish_reason = getattr(candidates[0], "finish_reason", None)
                # finish_reason.Name obtiene el enum de protobuf en formato string
                try:
                    finish_reason_name = candidates[0].finish_reason.name
                except Exception:
                    finish_reason_name = str(finish_reason)
            else:
                finish_reason_name = "NONE"

            # Captura tool calls + datos accesorios del history
            for content in getattr(session, "history", []) or []:
                for part in getattr(content, "parts", []) or []:
                    fc = getattr(part, "function_call", None)
                    fr = getattr(part, "function_response", None)
                    if fc is not None and getattr(fc, "name", ""):
                        args = dict(fc.args) if fc.args else {}
                        tool_calls.append({"name": fc.name, "args": args})
                        if fc.name == "execute_sql":
                            sql_text = args.get("sql_query", "")
                            sql_runs.append(sql_text)
                            try:
                                out = db.run_readonly_query(sql_text)
                                db_results.append(out)
                            except Exception:
                                pass
                        elif fc.name == "forecast_metrics":
                            mtrics = args.get("metrics") or []
                            if isinstance(mtrics, (int, float)):
                                mtrics = [str(mtrics)]
                            try:
                                fres = predictor.forecast_metrics(
                                    list(mtrics), int(args.get("periods") or 1))
                                forecasts.extend(fres["forecasts"])
                            except Exception as e:
                                logger.warning("recompute forecast fallo: %s", e)
            text = response.text.strip()
            elapsed = time.time() - start

            # --- Deteccion de truncamiento por limite de tokens ---
            truncated = (finish_reason_name == "MAX_TOKENS")
            if truncated and text and "Respuesta parcial" not in text:
                if not re.search(r"(\.{3}|…)\s*$", text):
                    text += " …"
                text += (
                    "\n\n> ⚠️ **Respuesta parcial** (alcanzó el máximo de "
                    "tokens de salida configurado por el desarrollador). "
                    "Reformulá más específicamente para recibir la respuesta completa."
                )

            return {
                "answer": text,
                "sql": sql_runs,
                "db_results": db_results,
                "forecasts": forecasts,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason_name,
                "truncated": truncated,
                "elapsed_s": round(elapsed, 2),
            }
        except Exception as e:
            last_exc = e
            wait = _extract_retry_after(e) or (2 if attempt == 0 else 5 + attempt * 5)
            logger.warning("Intento %d fallo (espera %ss): %s", attempt + 1, wait, e)
            if wait:
                time.sleep(wait)

    raise RuntimeError(f"El motor de IA no respondio tras 3 intentos: {last_exc}")


def test_gemini() -> tuple[bool, str]:
    try:
        models = [m.name for m in genai.list_models()
                  if "generateContent" in (m.supported_generation_methods or [])]
        ok = GEMINI_MODEL in models or any(GEMINI_MODEL in m for m in models)
        return ok, f"modelos disponibles: {len(models)}; {GEMINI_MODEL}={'OK' if ok else 'NO'}"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    print("=== Healthcheck Gemini ===")
    ok, msg = test_gemini()
    print(f"{ok} - {msg}")
    print("\n=== Test chat (ventas) ===")
    print(json.dumps(chat("Cuantas ventas tenemos al dia de la fecha?"),
                     ensure_ascii=False, indent=2, default=str))