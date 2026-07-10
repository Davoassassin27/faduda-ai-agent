"""
dlt_pipeline.py — Canal de datos WooCommerce → Google Sheets con dlt.

Usa dlt (data load tool) para:
  - Extracción desde la API REST de WooCommerce
  - Inferencia de esquema y validación de tipos
  - Carga incremental (solo productos nuevos/modificados)
  - Normalización de datos (precios, URLs, timestamps)

Arquitectura:
  Source: WooCommerce API (productos visibles)
  Pipeline: dlt orquesta extracción → normalización → carga
  Load: Google Sheets vía gspread (como custom destination)

Referencias:
  - dlt: https://dlthub.com/docs
  - WooCommerce REST API: https://woocommerce.github.io/woocommerce-rest-api-docs/
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Generator

import dlt
from dlt.common.typing import TDataItem
from woocommerce import API as WooAPI

import config as cfg

logger = logging.getLogger("wc.dlt_pipeline")

# ---------------------------------------------------------------------------
# dlt Source: WooCommerce Products
# ---------------------------------------------------------------------------

@dlt.source(max_table_nesting=1)
def woocommerce_source(
    api_url: str,
    consumer_key: str,
    consumer_secret: str,
) -> Generator[dlt.resource, Any, Any]:
    """
    dlt source que expone productos de WooCommerce como recurso.

    Configuración:
      - max_table_nesting=1: aplanamos 1 nivel (evita columnas anidadas)
      - primary_key="id": permite upsert incremental
      - write_disposition="merge": actualiza productos existentes
    """
    @dlt.resource(
        primary_key="id",
        write_disposition="merge",
        name="woocommerce_products",
        columns=[
            {"name": "id", "data_type": "bigint", "nullable": False},
            {"name": "name", "data_type": "text", "nullable": False},
            {"name": "regular_price", "data_type": "double", "nullable": True},
            {"name": "sale_price", "data_type": "double", "nullable": True},
            {"name": "price", "data_type": "double", "nullable": True},
            {"name": "images", "data_type": "json", "nullable": True},
            {"name": "sku", "data_type": "text", "nullable": True},
            {"name": "stock_status", "data_type": "text", "nullable": True},
            {"name": "categories", "data_type": "json", "nullable": True},
            {"name": "permalink", "data_type": "text", "nullable": True},
            {"name": "date_created", "data_type": "timestamp", "nullable": True},
            {"name": "date_modified", "data_type": "timestamp", "nullable": True},
        ],
    )
    def products(
        api_url: str,
        consumer_key: str,
        consumer_secret: str,
    ) -> Generator[TDataItem, Any, Any]:
        """
        Recurso dlt: extrae productos visibles desde WooCommerce.

        Incremental por defecto: dlt detecta cambios vía write_disposition
        y primary_key. En cada ejecución trae todos los productos y
        mergea según id.
        """
        wcapi = WooAPI(
            url=api_url,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            version="wc/v3",
            timeout=30,
        )

        page = 1
        total_pages = 1
        while page <= total_pages:
            logger.info("Extrayendo página %d de productos...", page)
            resp = wcapi.get("products", params={
                "per_page": 100,
                "page": page,
                "status": "publish",
            })

            if resp.status_code != 200:
                logger.error(
                    "WooCommerce API error %d: %s",
                    resp.status_code, resp.text[:200],
                )
                break

            data = resp.json()
            if not isinstance(data, list):
                break

            # Información de paginación
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            logger.info(
                "Página %d/%d — %d productos",
                page, total_pages, len(data),
            )

            for product in data:
                yield _normalize_product(product)

            page += 1
            # Pequeña pausa para no rate-limitear la API
            if page <= total_pages:
                time.sleep(0.3)

    return products(api_url, consumer_key, consumer_secret)


def _normalize_product(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normaliza un producto de WooCommerce al esquema plano del recurso.
    Extrae: id, nombre, precio, URL de imagen, SKU, stock, etc.
    """
    now = datetime.now(timezone.utc).isoformat()
    images = raw.get("images", [])
    first_image = images[0] if images else {}
    categories = raw.get("categories", [])
    first_cat = categories[0] if categories else {}

    return {
        "id": int(raw.get("id", 0)),
        "name": str(raw.get("name", "")),
        "sku": str(raw.get("sku", "") or ""),
        "regular_price": _parse_price(raw.get("regular_price")),
        "sale_price": _parse_price(raw.get("sale_price")),
        "price": _parse_price(raw.get("price")),
        "stock_status": str(raw.get("stock_status", "instock")),
        "image_url": str(first_image.get("src", "")),
        "image_alt": str(first_image.get("alt", "") or ""),
        "category_name": str(first_cat.get("name", "")),
        "category_slug": str(first_cat.get("slug", "")),
        "permalink": str(raw.get("permalink", "")),
        "description_truncated": str(raw.get("short_description", "") or "")[:500],
        "date_created": raw.get("date_created", now),
        "date_modified": raw.get("date_modified", now),
        "ingested_at": now,
    }


def _parse_price(val: Any) -> float | None:
    """Convierte precio string de WooCommerce a float."""
    if val is None or val == "":
        return None
    try:
        return round(float(str(val).replace(",", ".")), 2)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Ejecución del pipeline dlt
# ---------------------------------------------------------------------------

def run_pipeline(wc_cfg: cfg.WooCommerceConfig) -> dlt.LoadInfo:
    """
    Ejecuta el pipeline dlt completo.

    1. Extrae productos desde WooCommerce API
    2. Normaliza y valida según esquema definido
    3. Carga a destino DuckDB local (para auditoría/metadata)

    Retorna: dlt.LoadInfo con metadatos de la carga.
    """
    pipeline = dlt.pipeline(
        pipeline_name="woocommerce_sync",
        destination=dlt.destinations.duckdb(credentials="woocommerce_sync.duckdb"),
        dataset_name="fadua_products",
        progress="log",
    )

    source = woocommerce_source(
        api_url=wc_cfg.api_url,
        consumer_key=wc_cfg.consumer_key,
        consumer_secret=wc_cfg.consumer_secret,
    )

    logger.info("Ejecutando pipeline dlt...")
    load_info = pipeline.run(source)
    logger.info("Pipeline completado: %s", load_info)

    return load_info


def get_loaded_products() -> list[dict[str, Any]]:
    """
    Lee los productos cargados por dlt desde DuckDB.
    Retorna lista de dicts planos para escribir en Google Sheets.
    """
    try:
        import duckdb
        pipe = dlt.pipeline(
            "woocommerce_sync",
            destination=dlt.destinations.duckdb(credentials="woocommerce_sync.duckdb"),
        )
        con = duckdb.connect("woocommerce_sync.duckdb")
        schema = pipe.dataset_name
        rows = con.execute(
            f'SELECT id, name, price, regular_price, sale_price, '
            f'image_url, sku, stock_status, category_name, permalink, '
            f'date_created, date_modified, ingested_at '
            f'FROM "{schema}".woocommerce_products ORDER BY id'
        ).fetchall()
        cols = ["id", "name", "price", "regular_price", "sale_price",
                "image_url", "sku", "stock_status", "category_name",
                "permalink", "date_created", "date_modified", "ingested_at"]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.warning("No se pudo leer DuckDB: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    wc = cfg.WooCommerceConfig.from_env()
    info = run_pipeline(wc)
    print("Load info:", info)
    prods = get_loaded_products()
    print(f"Productos cargados: {len(prods)}")
    if prods:
        print("Primero:", prods[0]["name"], "—", prods[0]["price"])
