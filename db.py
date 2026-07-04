"""
db.py - Capa de acceso a MySQL con validador SQL de solo lectura.

Responsabilidades:
  * Conexion a MySQL (credenciales desde variables de entorno).
  * Introspeccion dinamica del esquema (SHOW TABLES / DESCRIBE), cacheada.
  * Validacion estricta de SQL: solo SELECT / WITH de lectura.
  * Ejecucion read-only con timeouts y manejo de errores.

Seguridad:
  Las credenciales vivien en .env (variables de entorno), nunca en el codigo.
  El LLM NO recibe credenciales; solo recibe nombres de tablas/columnas.
  Todo SQL generado por el LLM pasa por validate_sql antes de ejecutarse.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import pymysql
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# Configuracion de conexion (todas desde entorno)
# ---------------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "193.203.175.134")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "u420581741_pruebas")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "u420581741_pruebas")

CONNECT_TIMEOUT = 15
QUERY_TIMEOUT = 20  # segundos
MAX_ROWS = 200      # limite duro de filas devueltas al LLM

# ---------------------------------------------------------------------------
# Patrones de validacion SQL
# ---------------------------------------------------------------------------
# Palabras clave prohibidas (DML/DDL/inyeccion). Se comparan completa-palabra.
_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"REPLACE|RENAME|LOAD|OUTFILE|INFILE|CALL|EXEC|EXECUTE|HANDLER|"
    r"LOCK|UNLOCK|FLUSH|RESET|SHUTDOWN|KILL|SUPER|FILE|PROCESS|"
    r"SHOW\s+(?:GRANTS|PROCESSLIST|MASTER|SLAVE|LOGS|STATUS|VARIABLES)|"
    r"SET\s+PASSWORD|USE\s+mysql|INTO\s+OUTFILE|INTO\s+DUMPFILE"
    r")\b",
    re.IGNORECASE,
)

# Comentarios y terminadores peligrosos
_BAD_TOKENS = re.compile(r"(--|#|/\*|\*/|;|0x[0-9a-fA-F]+)", re.IGNORECASE)

# Debe empezar con SELECT o WITH (CTE de solo lectura)
_STARTS_OK = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class SQLValidationError(Exception):
    """SQL rechazado por el validador de seguridad."""


def validate_sql(sql: str) -> str:
    """
    Valida que `sql` sea una consulta de solo lectura segura.

    Reglas:
      1. Debe empezar con SELECT o WITH.
      2. No puede contener palabras prohibidas (INSERT, UPDATE, DROP, ...).
      3. No puede contener comentarios, punto y coma, ni literales hex.
      4. Se anade automaticamente LIMIT si no lo trae.

    Returns:
      sql saneado (limpio, con LIMIT).
    Raises:
      SQLValidationError si alguna regla falla.
    """
    if not sql or not sql.strip():
        raise SQLValidationError("Consulta vacia.")

    s = sql.strip().rstrip(";").strip()

    if not _STARTS_OK.match(s):
        raise SQLValidationError(
            "Solo se permiten consultas SELECT (o WITH ... SELECT)."
        )

    # Bloquea comentarios y punto y coma embebidos
    bad = _BAD_TOKENS.search(s)
    if bad:
        raise SQLValidationError(
            f"Token no permitido en la consulta: '{bad.group(0)}'."
        )

    # Palabras prohibidas completas
    forbidden_hit = _FORBIDDEN.search(s)
    if forbidden_hit:
        raise SQLValidationError(
            f"Operacion no permitida en modo solo lectura: '{forbidden_hit.group(1)}'."
        )

    # Limita el tamano para evitar abusos
    if len(s) > 4000:
        raise SQLValidationError("Consulta demasiado larga (max 4000 chars).")

    # Anade LIMIT si no lo tiene
    if not re.search(r"\bLIMIT\s+\d+", s, re.IGNORECASE):
        s = f"{s} LIMIT {MAX_ROWS}"
    else:
        # Reescribe el LIMIT si es mayor al permitido
        s = re.sub(
            r"\bLIMIT\s+\d+(?:\s*,\s*\d+)?",
            f"LIMIT {MAX_ROWS}",
            s,
            count=1,
            flags=re.IGNORECASE,
        )

    return s


# ---------------------------------------------------------------------------
# Conexion
# ---------------------------------------------------------------------------
def get_connection() -> pymysql.connections.Connection:
    """Crea una conexion fresca a MySQL usando las credenciales de entorno."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=QUERY_TIMEOUT,
        write_timeout=QUERY_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Introspeccion del esquema (cacheada)
# ---------------------------------------------------------------------------
_SCHEMA_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_SCHEMA_TTL = 300  # recarga cada 5 min (en segundos)


def get_schema_description() -> str:
    """
    Devuelve una descripcion textual del esquema para inyectar en el prompt
    del LLM. Cacheada por 5 minutos para no golpear la BD en cada mensaje.

    Formato ejemplo:
      TABLA metricas_campanas_ventas:
        - fecha (date)
        - google_ads_leads (int)
        ...
    """
    now = time.time()
    if _SCHEMA_CACHE["data"] is None or now - _SCHEMA_CACHE["ts"] > _SCHEMA_TTL:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES")
                tables = [list(r.values())[0] for r in cur.fetchall()]
                desc: list[str] = []
                for t in tables:
                    cur.execute(f"DESCRIBE `{t}`")
                    cols = cur.fetchall()
                    lines = [f"TABLA `{t}`:"]
                    for c in cols:
                        field = c.get("Field", "")
                        ctype = c.get("Type", "")
                        lines.append(f"  - {field} ({ctype})")
                    # Rango de fechas y conteo (ayuda al LLM)
                    try:
                        cur.execute(f"SELECT COUNT(*) AS n FROM `{t}`")
                        n = cur.fetchone()["n"]
                        lines.append(f"  (filas totales: {n})")
                    except Exception:
                        pass
                    desc.append("\n".join(lines))
                _SCHEMA_CACHE["data"] = "\n\n".join(desc)
                _SCHEMA_CACHE["ts"] = now
        finally:
            conn.close()
    return _SCHEMA_CACHE["data"]


# ---------------------------------------------------------------------------
# Ejecucion read-only
# ---------------------------------------------------------------------------
def run_readonly_query(sql: str) -> dict[str, Any]:
    """
    Valida y ejecuta un SELECT de solo lectura.

    Returns:
      {"columns": [...], "rows": [...]}
    Raises:
      SQLValidationError si el SQL no pasa validacion.
      RuntimeError si hay error de BD o timeout.
    """
    safe_sql = validate_sql(sql)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(safe_sql)
            rows = cur.fetchall()
        columns = list(rows[0].keys()) if rows else []
        return {"columns": columns, "rows": rows}
    except pymysql.err.OperationalError as e:
        # Timeouts / conexion perdida
        raise RuntimeError(f"Error de base de datos: {e}") from e
    except pymysql.err.MySQLError as e:
        raise RuntimeError(f"Error de SQL: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_connection() -> tuple[bool, str]:
    """Healthcheck: prueba conexion y devuelevanto 1."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            r = cur.fetchone()
        conn.close()
        return True, f"OK ({r['ok'] if r else 'sin dato'})"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    print("=== Healthcheck ===")
    ok, msg = test_connection()
    print(f"conexion: {ok} - {msg}")
    print("\n=== Esquema ===")
    print(get_schema_description())
    print("\n=== Validador SQL ===")
    tests = [
        "SELECT COUNT(*) FROM metricas_campanas_ventas",
        "SELECT * FROM metricas_campanas_ventas LIMIT 3",
        "DELETE FROM metricas_campanas_ventas",
        "SELECT * FROM metricas_campanas_ventas; DROP TABLE metricas_campanas_ventas",
        "WITH t AS (SELECT SUM(cantidad_ventas) v FROM metricas_campanas_ventas) SELECT * FROM t",
    ]
    for t in tests:
        try:
            safe = validate_sql(t)
            print(f"OK  -> {safe}")
        except SQLValidationError as e:
            print(f"BLOQUEADO ({e}) <- {t}")