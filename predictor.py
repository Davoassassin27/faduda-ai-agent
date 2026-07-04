"""
predictor.py - Forecast de metricas con SARIMAX (statsmodels).

Filosofia de degradacion graceful (defensible en la entrevista):
  * >=12 meses: SARIMAX con estacionalidad 12 (modela tendencia + estacionalidad).
  * 6-11 meses: SARIMAX simple sin estacionalidad + disclaimer.
  * <6 meses: regresion lineal sobre el indice + disclaimer fuerte.
  * Errores de convergencia: cae a media movil simple de los ultimos 3 meses.

Toda prediccion devuelve INTERVALOS de confianza (80% y 95%) para comunicar
la incertidumbre de forma honesta: un buen forecast dice cuánto NO sabe.

Justificacion metodologica (punto 3 de la defensa):
  SARIMA/SARIMAX es el estandar estadistico para series univariadas con
  tendencia y estacionalidad. Elegido sobre Prophet porque:
    - No requiere compilador C++ (Prophet si, problematico en Windows).
    - Parametros transparentes (AIC seleccionable), mas defendible.
    - statsmodels ya instalado y estable cross-platform.
  Prophet/Abacus.AI se mencionan como alternativa empresarial para datasets
  mas grandes con tuning automatico.
"""
from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

import db

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapeo de nombres amigables -> columna SQL + agregacion
# ---------------------------------------------------------------------------
# El LLM pedira "leads", "ventas", "ingresos". Mapeamos a columnas reales y
# la funcion de agregacion adecuada (suma para volumenes).
METRIC_MAP: dict[str, dict[str, str]] = {
    "ventas": {"col": "cantidad_ventas", "agg": "SUM"},
    "cantidad_ventas": {"col": "cantidad_ventas", "agg": "SUM"},
    "leads": {"col": "total_leads", "agg": "SUM"},
    "total_leads": {"col": "total_leads", "agg": "SUM"},
    "leads_google": {"col": "google_ads_leads", "agg": "SUM"},
    "leads_meta": {"col": "meta_ads_leads", "agg": "SUM"},
    "ingresos": {"col": "ingresos_ventas_usd", "agg": "SUM"},
    "ingresos_ventas_usd": {"col": "ingresos_ventas_usd", "agg": "SUM"},
    "impresiones_google": {"col": "google_ads_impresiones", "agg": "SUM"},
    "impresiones_meta": {"col": "meta_ads_impresiones", "agg": "SUM"},
    "clics_google": {"col": "google_ads_clics", "agg": "SUM"},
    "clics_meta": {"col": "meta_ads_clics", "agg": "SUM"},
    "costo_google": {"col": "google_ads_costo_usd", "agg": "SUM"},
    "costo_meta": {"col": "meta_ads_costo_usd", "agg": "SUM"},
}


def resolve_metric(metric: str) -> dict[str, str]:
    """Normaliza el nombre de metrica pedido por el LLM a una columna SQL."""
    key = (metric or "").strip().lower().replace(" ", "_")
    if key in METRIC_MAP:
        return METRIC_MAP[key]
    # fallback: si coincide exacto con una columna, asumimos SUM
    raise ValueError(
        f"Metrica '{metric}' no reconocida. Validas: "
        + ", ".join(sorted(METRIC_MAP.keys()))
    )


# ---------------------------------------------------------------------------
# Carga de serie mensual desde MySQL
# ---------------------------------------------------------------------------
def load_monthly_series(metric: str) -> pd.Series:
    """
    Agrega la metrica por mes completo (YEAR-MONTH) y devuelve una Serie
    pandas indexada por fines de mes, ordenada.
    """
    spec = resolve_metric(metric)
    col = spec["col"]
    agg = spec["agg"]
    table = "metricas_campanas_ventas"

    sql = (
        f"SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes, "
        f"{agg}(`{col}`) AS valor "
        f"FROM `{table}` "
        f"GROUP BY DATE_FORMAT(fecha, '%Y-%m') "
        f"ORDER BY mes ASC "
        f"LIMIT {db.MAX_ROWS}"
    )

    # Reusamos el validador de db.py para mantener seguridad.
    result = db.run_readonly_query(sql)
    if not result["rows"]:
        raise RuntimeError("No hay datos historicos para esa metrica.")

    df = pd.DataFrame(result["rows"])
    df["mes"] = pd.to_datetime(df["mes"] + "-01", errors="coerce")
    df = df.dropna(subset=["mes"]).sort_values("mes").reset_index(drop=True)
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").fillna(0.0)

    serie = pd.Series(df["valor"].values, index=pd.PeriodIndex(df["mes"], freq="M"))
    return serie


# ---------------------------------------------------------------------------
# Modelos de forecast con degradacion graceful
# ---------------------------------------------------------------------------
def _sanitize_intervals(
    point: np.ndarray,
    ci80: np.ndarray,
    ci95: np.ndarray,
    values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Garantiza intervalos validos y honestos. SARIMAX puede devolver NaN
    o intervalos degenerados con pocos datos. Si eso pasa, reconstruye
    los intervalos a partir de un sigma de seguridad = max(std historica,
    5% del ultimo valor), y siempre lower<=point<=upper.
    """
    last_val = abs(values[-1]) if len(values) else 1.0
    sigma = float(np.std(values)) if len(values) > 1 else 0.05 * last_val
    sigma = max(sigma, 0.05 * (last_val or 1.0), 1e-6)

    def band(ci, alpha_z):
        # ci es (periods, 2)
        out = np.asarray(ci, dtype=float)
        bad = ~np.isfinite(out).all(axis=1) if out.ndim == 2 else ~np.isfinite(out)
        if out.ndim != 2:
            out = np.column_stack([point - alpha_z * sigma, point + alpha_z * sigma])
        # detecta NaN/inf o bandas degeneradas (ancho aprox 0)
        for i in range(len(point)):
            row = out[i]
            if not np.isfinite(row).all() or abs(row[1] - row[0]) < 1e-9:
                out[i, 0] = point[i] - alpha_z * sigma
                out[i, 1] = point[i] + alpha_z * sigma
        return out

    ci80 = band(ci80, 1.282)
    ci95 = band(ci95, 1.96)
    # lower <= upper
    ci80 = np.column_stack([np.minimum(ci80[:, 0], ci80[:, 1]),
                            np.maximum(ci80[:, 0], ci80[:, 1])])
    ci95 = np.column_stack([np.minimum(ci95[:, 0], ci95[:, 1]),
                            np.maximum(ci95[:, 0], ci95[:, 1])])
    return ci80[:, 0], ci80[:, 1], ci95[:, 0], ci95[:, 1]


def forecast_sarimax(serie: pd.Series, periods: int = 1) -> dict[str, Any]:
    """
    Forecast robusto con degradacion:
      - SARIMAX estacional (1,1,1)x(1,1,1,12) si >=24 meses (2 ciclos).
      - SARIMAX no estacional (1,1,1) si 12-23 meses.
      - Regresion lineal si <12 meses.
      - Media movil si todo falla.

    Nota: Prophet (Meta) fue evaluado pero descartado para la demo en vivo
    porque el fit tarda ~30s por modelo y haria la respuesta lenta. SARIMAX
    es ~100x mas rapido y suficientemente robusto para 18 meses de datos.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    n = len(serie)
    values = np.asarray(serie.values, dtype=float)

    # --- 1) SARIMAX estacional (>=24 meses: dos ciclos anuales) ---
    if n >= 24:
        try:
            model = SARIMAX(
                values,
                order=(1, 1, 1),
                seasonal_order=(1, 1, 1, 12),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            fit = model.fit(disp=False, maxiter=150)
            fc = fit.get_forecast(steps=periods)
            point = np.asarray(fc.predicted_mean, dtype=float)
            ci80 = np.asarray(fc.conf_int(alpha=0.20), dtype=float)
            ci95 = np.asarray(fc.conf_int(alpha=0.05), dtype=float)
            l80, u80, l95, u95 = _sanitize_intervals(point, ci80, ci95, values)
            return {
                "forecast": np.nan_to_num(point, nan=float(values[-1])).tolist(),
                "lower_80": l80.tolist(),
                "upper_80": u80.tolist(),
                "lower_95": l95.tolist(),
                "upper_95": u95.tolist(),
                "method": "SARIMAX(1,1,1)x(1,1,1,12) estacional",
                "n_history": n,
            }
        except Exception as e:
            logger.warning("SARIMAX estacional fallo (%s), cae a no estacional.", e)

    # --- 2) SARIMAX no estacional (>=12 meses) ---
    if n >= 12:
        try:
            model = SARIMAX(
                values,
                order=(1, 1, 1),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            fit = model.fit(disp=False, maxiter=150)
            fc = fit.get_forecast(steps=periods)
            point = np.asarray(fc.predicted_mean, dtype=float)
            ci80 = np.asarray(fc.conf_int(alpha=0.20), dtype=float)
            ci95 = np.asarray(fc.conf_int(alpha=0.05), dtype=float)
            l80, u80, l95, u95 = _sanitize_intervals(point, ci80, ci95, values)
            return {
                "forecast": np.nan_to_num(point, nan=float(values[-1])).tolist(),
                "lower_80": l80.tolist(),
                "upper_80": u80.tolist(),
                "lower_95": l95.tolist(),
                "upper_95": u95.tolist(),
                "method": "SARIMAX(1,1,1) no estacional",
                "n_history": n,
            }
        except Exception as e:
            logger.warning("SARIMAX no estacional fallo (%s), cae a regresion.", e)

    # --- 3) Regresion lineal sobre el indice (<6 meses o fallback) ---
    try:
        x = np.arange(n, dtype=float)
        y = values
        # y = a + b*x  (minimos cuadrados)
        coeffs = np.polyfit(x, y, 1)
        poly = np.poly1d(coeffs)
        future_x = np.arange(n, n + periods, dtype=float)
        point = poly(future_x)
        # Intervalo approximado con std de residuos
        residuals = y - poly(x)
        sigma = float(np.std(residuals)) if n > 1 else float(np.std(values) or 1.0)
        return {
            "forecast": point.tolist(),
            "lower_80": (point - 1.282 * sigma).tolist(),
            "upper_80": (point + 1.282 * sigma).tolist(),
            "lower_95": (point - 1.96 * sigma).tolist(),
            "upper_95": (point + 1.96 * sigma).tolist(),
            "method": "Regresion lineal (pocos datos)",
            "n_history": n,
        }
    except Exception as e:
        logger.warning("Regresion lineal fallo (%s), cae a media movil.", e)

    # --- 4) Media movil de ultimos 3 meses (ultimo recurso) ---
    last3 = values[-3:] if len(values) >= 3 else values
    base = float(np.mean(last3))
    sigma = float(np.std(values)) if len(values) > 1 else 1.0
    point = np.full(periods, base, dtype=float)
    return {
        "forecast": point.tolist(),
        "lower_80": (point - 1.282 * sigma).tolist(),
        "upper_80": (point + 1.282 * sigma).tolist(),
        "lower_95": (point - 1.96 * sigma).tolist(),
        "upper_95": (point + 1.96 * sigma).tolist(),
        "method": "Media movil (fallback seguro)",
        "n_history": n,
    }


# ---------------------------------------------------------------------------
# API publica del predictor
# ---------------------------------------------------------------------------
def forecast_metric(metric: str, periods: int = 1) -> dict[str, Any]:
    """
    Forecast de UNA metrica para `periods` meses futuros (mantenido por
    retrocompatibilidad). Internamente rutea a forecast_metrics.
    """
    return forecast_metrics([metric], periods)["forecasts"][0]


# ---------------------------------------------------------------------------
# Batch + cache (reduce hits a MySQL y latencia para el caso predictivo)
# ---------------------------------------------------------------------------
import time as _time
import threading

_FC_CACHE: dict[tuple, dict] = {}
_FC_LOCK = threading.Lock()
_FC_TTL = 300  # 5 min

def forecast_metrics(metrics: list[str], periods: int = 1) -> dict[str, Any]:
    """
    Forecast de varias metricas en una sola invocacion (batched).

    Cachea por 5 minutos por (tuple(metrics_sorted), periods) para evitar
    recomputar al repreguntar. Devuelve:

      {
        "forecasts": [ {...}, {...}, ... ],   # uno por metrica
        "periods": int,
        "disclaimer": str,                    # disclaimer comun
        "cached": bool,
      }
    """
    periods = max(1, min(int(periods or 1), 12))
    metrics = [m for m in metrics if m]
    if not metrics:
        raise ValueError("No se especificaron metricas.")

    key = (tuple(sorted(metrics)), periods)
    now = _time.time()
    with _FC_LOCK:
        cached = _FC_CACHE.get(key)
        if cached and now - cached["ts"] < _FC_TTL:
            return {"forecasts": cached["payload"], "periods": periods,
                    "disclaimer": cached["disclaimer"], "cached": True}
        forecasts = []
        for m in metrics:
            try:
                serie = load_monthly_series(m)
                res = forecast_sarimax(serie, periods=periods)
                last_period = serie.index[-1]
                future_periods = [str((last_period + i).to_timestamp().strftime("%Y-%m"))
                                  for i in range(1, periods + 1)]
                res.update({
                    "metric": m,
                    "periods": periods,
                    "projected_months": future_periods,
                    "last_historical_value": float(serie.values[-1]),
                    "last_historical_month": str(last_period),
                    # historico para graficar (desde load_monthly_series por mes)
                    "history_months": [str(p) for p in serie.index],
                    "history_values": [float(v) for v in serie.values],
                })
                n = res["n_history"]
                if n < 12:
                    res["disclaimer"] = (
                        f"Forecast sobre {n} meses (menos de 12): indicativo, no "
                        f"accionable. Modela tendencia pero no estacionalidad."
                    )
                else:
                    res["disclaimer"] = (
                        f"Modelo {res['method']} sobre {n} meses. Los intervalos "
                        f"reflejan incertidumbre; el valor real puede caer fuera "
                        f"del 80% ante cambios estructurales del mercado."
                    )
                forecasts.append(res)
            except Exception as e:
                forecasts.append({"metric": m, "error": str(e)})

        disclaimer = (
            "Pronostico estadistico SARIMAX. Los intervalos de confianza "
            "expresan incertidumbre real; tratar como informacion de apoyo, "
            "no como orden automatica."
        )
        with _FC_LOCK:
            _FC_CACHE[key] = {"payload": forecasts, "ts": now, "disclaimer": disclaimer}

    return {"forecasts": forecasts, "periods": periods, "disclaimer": disclaimer, "cached": False}


if __name__ == "__main__":
    import json

    print("=== Forecast VENTAS (1 mes) ===")
    print(json.dumps(forecast_metric("ventas", 1), indent=2, default=str))
    print("\n=== Forecast LEADS (1 mes) ===")
    print(json.dumps(forecast_metric("leads", 1), indent=2, default=str))