# Desafío 2 — Agente Autónomo para Google Forms

Agente con IA que lee datos desde **Google Sheets** y completa automáticamente **Google Forms** usando **Playwright** + **Gemini** con patrón **RAG**, más dashboard TUI en terminal.

## Stack

| Capa | Tecnología | Rol |
|------|-----------|-----|
| Browser | **Playwright** (Chromium) | Navegación headless/headful, `fill()` nativo, captura de pantalla por registro |
| LLM | **Gemini 2.5 Flash** | Mapeo semántico campos → columnas vía function calling |
| Sheets | **gspread** | Lectura de datos fuente + columnas |
| RAG | **Prompt contextual** | Retrieve (HTML del form) → Augment (columnas sheet) → Generate (mapeo JSON) |
| TUI | **Rich** | Dashboard en vivo con detección, mapeo y progreso por registro |

## Pipeline RAG

```
1. Retrieve
   BrowserAgent navega al Google Forms y extrae los campos visibles
   (labels, inputs, listboxes) página por página.

2. Augment
   SheetReader provee cabeceras del sheet + datos de muestra.
   Contexto completo: "formulario con campos [X, Y, Z] y sheet con columnas [A, B, C]".

3. Generate
   Gemini recibe el contexto y devuelve un mapping JSON estructurado:
   [{"form_field": "...", "sheet_column": "...", "confidence": 0.95}]
```

## Formularios

- **Form 1** (Ventas): https://forms.gle/oqjtULJ6iGBT7HFR7
- **Form 2** (Mora): https://forms.gle/JQTABscuZxn2S6Dh7
- **Sheet fuente**: https://docs.google.com/spreadsheets/d/1y6aREOjFrbDd5bKlpt72UBc6svk_pr2wBsAqv_xb_2Y

## Modos de ejecución

```bash
# Headless (sin ventana)
python agent.py

# Con navegador visible (debug)
python agent.py --visible

# Solo un formulario
python agent.py --form 1
python agent.py --form 2

# Solo mapear campos, sin enviar
python agent.py --dry-run

# Modo texto plano (sin TUI)
python agent.py --no-tui
```

## TUI Dashboard

Al ejecutar sin `--no-tui`, `agent.py` muestra un panel en vivo:

- **Fase actual**: init → sheet → detect → map → fill → done, con ícono
- **Campos detectados**: lista con indicador de mapeo (✓/○)
- **Mapeo RAG**: tabla campo form → columna sheet + confianza
- **Progreso registros**: barra + caracteres por registro (✓/✗/◌/·)
- **Errores**: últimos 5 errores en panel rojo
- **Reporte final**: resumen con tabla de mappings y estadísticas

## Archivos clave

| Archivo | Función |
|---------|---------|
| `agent.py` | Orquestador principal (FormAgent + CLI) |
| `browser_agent.py` | Navegación, detección, relleno, envío con Playwright |
| `field_mapper.py` | Mapeo RAG via Gemini (retrieve → augment → generate) |
| `sheet_reader.py` | Lectura de Google Sheets (estructurada + registros) |
| `config.py` | Config vía .env con dataclasses (gemini, sheets, forms) |
| `tui.py` | Dashboard Rich para terminal |

## Demo

### Requisitos
```bash
pip install -r requirements.txt
playwright install chromium
```

### Configuración
```bash
cp .env.example .env
# Editar: GEMINI_API_KEY, GSHEETS_SPREADSHEET_ID
# Colocar service_account.json en la raíz
```

## Lo que demuestra

- **Patrón RAG** sin vector DB: retrieve (HTML del form) → augment (columnas sheet) → generate (Gemini)
- **Browser automation**: fill dinámico, detección multi-página, captura por registro
- **IA generativa**: mapeo semántico campo→columna con confidence score y reasoning
- **Producción**: manejo de errores, logging estructurado, reporte JSON, graceful degradation
- **Observabilidad**: Rich TUI con 4 paneles en vivo + reporte final estructurado
- **Multi-formulario**: soporte para N formularios con N registros cada uno

---

**Desarrollado por David Soler** — [MIT License](../LICENSE)
