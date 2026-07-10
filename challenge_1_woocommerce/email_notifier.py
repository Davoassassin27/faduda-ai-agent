"""
email_notifier.py — Notificación por correo con resumen de la sincronización.

Usa SMTP con TLS. Configurable vía .env (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD).
El destinatario se define en NOTIFY_EMAIL (default: tejada.ca23@gmail.com).
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import config as cfg

logger = logging.getLogger("wc.email")


def build_summary_html(
    products: list[dict[str, Any]],
    previous_count: int,
    elapsed_s: float,
) -> str:
    """
    Construye el cuerpo HTML del email con tabla de productos procesados.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_count = len(products)
    diff = new_count - previous_count

    rows_html = ""
    for p in products[:20]:  # máx 20 filas en el email
        name = p.get("name", "")
        price = p.get("price", "")
        img = p.get("image_url", "")
        img_tag = f'<img src="{img}" width="50" height="50" style="border-radius:4px;object-fit:cover"/>' if img else ""
        rows_html += f"""<tr>
            <td>{p.get("id", "")}</td>
            <td>{img_tag}</td>
            <td>{name[:60]}</td>
            <td>${price}</td>
            <td>{p.get("stock_status", "")}</td>
        </tr>"""

    if len(products) > 20:
        rows_html += f"<tr><td colspan='5'>… y {len(products) - 20} más</td></tr>"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
  body {{ font-family: -apple-system, sans-serif; color: #1a1a2e; padding: 20px; }}
  .summary {{ background: #f0f4ff; padding: 16px; border-radius: 8px; margin-bottom: 20px; }}
  .summary h2 {{ margin: 0 0 8px; color: #2563eb; font-size: 16px; }}
  .summary p {{ margin: 2px 0; font-size: 13px; color: #475569; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
  th {{ background: #2563eb; color: #fff; padding: 8px 10px; text-align: left; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }}
  tr:hover td {{ background: #f8fafc; }}
  .footer {{ margin-top: 20px; font-size: 11px; color: #94a3b8; }}
</style></head>
<body>
<div class="summary">
  <h2>📦 Sincronización WooCommerce → Google Sheets</h2>
  <p><b>Ejecución:</b> {now}</p>
  <p><b>Productos activos:</b> {new_count}</p>
  <p><b>Cambio vs ejecución anterior:</b> {diff:+d}</p>
  <p><b>Tiempo de proceso:</b> {elapsed_s:.1f}s</p>
</div>
<table>
  <thead><tr><th>ID</th><th></th><th>Producto</th><th>Precio</th><th>Stock</th></tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<div class="footer">
  <p>Pipeline dlt · FADUA WooCommerce Sync · {now}</p>
</div>
</body>
</html>"""
    return html


def send_notification(
    products: list[dict[str, Any]],
    previous_count: int,
    elapsed_s: float,
    email_cfg: cfg.EmailConfig,
) -> bool:
    """
    Envía el email de resumen vía SMTP.

    Retorna True si se envió correctamente, False si hubo error.
    """
    if not email_cfg.user or not email_cfg.password:
        logger.warning("SMTP no configurado. Email no enviado.")
        return False

    html = build_summary_html(products, previous_count, elapsed_s)
    subject = f"📦 WooCommerce Sync — {len(products)} productos activos"

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = email_cfg.user
    msg["To"] = email_cfg.notify_to

    try:
        with smtplib.SMTP(email_cfg.host, email_cfg.port, timeout=15) as server:
            server.starttls()
            server.login(email_cfg.user, email_cfg.password)
            server.sendmail(email_cfg.user, [email_cfg.notify_to], msg.as_string())
        logger.info("Email enviado a %s", email_cfg.notify_to)
        return True
    except Exception as e:
        logger.error("Fallo envío de email: %s", e)
        return False
