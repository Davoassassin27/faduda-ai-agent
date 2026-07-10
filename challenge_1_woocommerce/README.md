# Desafío 1 — WooCommerce → Google Sheets

Pipeline ETL que cada **5 minutos** sincroniza productos desde la API REST de WooCommerce hacia Google Sheets, con notificación por email y dashboard TUI en terminal.

## Stack

| Capa | Tecnología | Rol |
|------|-----------|-----|
| Extract | **dlt** (verified source) | Extracción incremental con inferencia de esquema y merge |
| Stage | **DuckDB** | Base embebida sin servidor para staging plano |
| Presentación | **gspread** | Google Sheets API v4 — escritura atómica (truncate + rewrite) |
| Notificación | **smtplib** + MIME | Email con tabla HTML de productos sincronizados |
| TUI | **Rich** | Dashboard en vivo con indicadores de paso y countdown |

## Arquitectura

```
WooCommerce API
     ↓  dlt (incremental merge, schema inference)
  DuckDB (staging)
     ↓  gspread (truncate + rewrite)
  Google Sheets
     ↓  SMTP
  Email a tejada.ca23@gmail.com
     ↓
  Rich TUI Dashboard (terminal)
```

## Modos de ejecución

```bash
# Una sola ejecución (con TUI)
python scheduler.py

# Loop infinito cada 5 min (modo daemon)
python scheduler.py --daemon

# Sin TUI (para logs crudos o cron)
python scheduler.py --no-tui

# Generar línea de crontab
python scheduler.py --cron
```

## TUI Dashboard

Al ejecutar sin flags, `scheduler.py` muestra un panel en vivo con:

- **Pipeline Steps**: 3 pasos (dlt, sheets, email) con indicador de estado ✓/✗ y tiempo
- **Métricas**: conteo de productos, diff vs ciclo anterior, estado general
- **Modo daemon**: cuenta regresiva visual entre ciclos
- **Footer**: timestamp UTC + "Desarrollado por David Soler"

## Demo

### Requisitos
```bash
pip install -r requirements.txt
playwright install chromium  # solo para desafío 2
```

### Configuración
```bash
cp .env.example .env
# Editar: WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET,
#         GSHEETS_SPREADSHEET_ID, NOTIFY_EMAIL, SMTP_*
# Colocar service_account.json en la raíz
```

### Archivos clave

| Archivo | Función |
|---------|---------|
| `scheduler.py` | Orquestador principal (único, daemon, cron, TUI) |
| `dlt_pipeline.py` | Source dlt para WooCommerce + normalización |
| `sheets_sync.py` | Sincronización a Google Sheets (truncate + rewrite) |
| `email_notifier.py` | Notificación SMTP con tabla HTML |
| `config.py` | Config vía .env con dataclasses |
| `tui.py` | Dashboard Rich para terminal |

## Lo que demuestra

- **dlt hub**: source verificado, inferencia de esquema, merge incremental
- **gspread**: escritura atómica con cabeceras fijas + formateo condicional
- **Email HTML**: tabla de productos, diff vs ejecución anterior
- **Producción**: config vía .env, logging, manejo de errores graceful
- **Observabilidad**: Rich TUI con 3 niveles de detalle (TUI, log, JSON report)
- **Arquitectura limpia**: separación clara de capas (extract → stage → presentación)

---

**Desarrollado por David Soler** — [MIT License](../LICENSE)
