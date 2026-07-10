# FADUA · AI Agent for Campaign Analytics

**Conversational BI agent** that answers natural-language questions about advertising KPIs (Google Ads, Meta Ads, leads, sales, revenue) from a MySQL database, generates **SARIMAX forecasts** with confidence intervals, and renders interactive charts — all through a single-page chat UI.

Built for the FADUA / Grupo Yumak AI Management interview.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Browser (index.html)                      │
│  Vanilla JS · marked.js (markdown) · Chart.js (forecast)    │
└────────────────────────┬────────────────────────────────────┘
                         │ POST /chat { question }
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI (main.py)                         │
│                                                             │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐   │
│  │ /health  │   │ /chat        │   │ /schema  /predict │   │
│  │ /metrics │   │ (orchestrate) │   │ (aux endpoints)  │   │
│  └──────────┘   └──────┬───────┘   └───────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼────────────────────────────────┐   │
│  │              llm.py · Gemini Agent                     │   │
│  │  System prompt = schema injection + style guide        │   │
│  │  Auto function calling (google-generativeai)           │   │
│  │  Tools:                                                │   │
│  │    • execute_sql(sql) → db.py → MySQL (read-only)      │   │
│  │    • forecast_metrics(metrics) → predictor.py → SARIMAX│   │
│  │  Truncation detection → user-facing warning            │   │
│  │  Retry with backoff (429/5xx)                          │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Data flow

1. User types a question in natural Spanish
2. Gemini receives the question + schema (injected at startup) + style rules
3. LLM decides: call `execute_sql` (query real data) or `forecast_metrics` (predict)
4. The backend runs the tool, returns structured results
5. Gemini synthesizes the answer in markdown, quoting real figures
6. Frontend renders: markdown answer + SQL panel + data table + chart + forecast bands

---

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | **Python 3.11 + FastAPI** | Async-capable, automatic OpenAPI docs, Pydantic validation |
| LLM | **Google Gemini 2.5 Flash** | Free tier capable; function calling built-in; fast inference |
| Database | **MySQL 8 + PyMySQL** | Target database; pure-Python driver, no native deps |
| Forecasting | **statsmodels SARIMAX** | ~1s per model vs Prophet's ~30s; transparent AIC tuning; cross-platform |
| Frontend | **Vanilla HTML/CSS/JS** | Zero build step, single-file deploy, CDN dependencies only |
| Auth/Security | **SQL validator + .env** | Strict SELECT-only parser; credentials never reach the LLM |

### Why not…

- **Prophet** → 30s+ fit time kills demo flow; compiled C++ dependency problematic on Windows
- **LangChain** → Added latency/abstraction overhead; raw function calling is simpler and more debuggable
- **Next.js / React** → Unnecessary for a single-page chat UI; build step complicates instant deploy
- **XGBoost / ML models** → ~18 months of data is insufficient; SARIMAX is the correct bias-variance tradeoff

---

## Project Structure

```
faduda/
├── main.py              # FastAPI app: CORS, endpoints /chat /health /predict /schema
├── llm.py               # Gemini agent: system prompt, function calling, truncation detection
├── db.py                # MySQL connection, schema introspection, SQL validator
├── predictor.py         # SARIMAX forecast with graceful degradation (4 tiers)
├── static/
│   └── index.html       # Chat UI: markdown, tables, charts, confidence bands
├── RESPONSE_STYLE.md    # Markdown style guide (injected into system prompt)
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── .gitignore
```

---

## Key Design Decisions

### 1. Agentic tool use (not text generation)

The LLM does not generate free-text SQL or fake numbers. It calls **backend-controlled tools**:

- **`execute_sql(sql_query)`** — validated by `db.validate_sql()` (SELECT/WITH only). The schema is injected into the system prompt so the LLM knows table/column names without ever seeing credentials.
- **`forecast_metrics(metrics, periods)`** — batched prediction for multiple metrics in a single tool call. Returns point forecast + 80%/95% confidence bands + historical series for chart rendering.

This pattern guarantees:
- No SQL injection (DML/DDL blocked before reaching MySQL)
- No hallucinated numbers (forecasts come from statsmodels, not the LLM)
- Auditability (every SQL and forecast is captured and returned to the frontend)

### 2. Forecasting with graceful degradation

| Data available | Model | Disclaimer |
|---|---|---|
| ≥24 months | SARIMAX(1,1,1)×(1,1,1,12) seasonal | Full |
| 12–23 months | SARIMAX(1,1,1) non-seasonal | "No estacional" |
| 6–11 months | Linear regression | "Indicativo, no accionable" |
| <6 months / convergence failure | Moving average (last 3) | "Fallback seguro" |

Confidence intervals are sanitized post-fit: if SARIMAX returns NaN or degenerate bands, they are reconstructed using `σ = max(std(history), 5% of last value)`.

### 3. Security model

- **SQL validator** (`db.validate_sql`): strict whitelist of SELECT/WITH only. Blocks `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE`, comments (`--`, `/*`), hex literals, `INTO OUTFILE`, and multi-statement `;`. Enforces `LIMIT` (max 200 rows) and max length (4000 chars). Tested: *"Borra la tabla"* → rejected.
- **Credentials**: in `.env` only, never in code. `.env` is in `.gitignore`.
- **CORS**: configurable via `ALLOWED_ORIGIN` env var.
- **API key isolation**: the LLM never receives MySQL credentials — only the schema (column names).

### 4. Error handling

| Scenario | Strategy |
|---|---|
| LLM generates invalid SQL | Gemini retries with the error message as feedback (SDK-managed, up to 3 attempts) |
| Database timeout/connection lost | 20s read timeout → HTTP 503 with user-facing Spanish message |
| Gemini quota exhausted (429) | 3 retries with exponential backoff (respects `retry_delay` from API response) |
| Insufficient data for forecast | Falls through 4 degradation tiers; each tier outputs an honest disclaimer |
| Token limit reached | `finish_reason == MAX_TOKENS` detected → append "(Respuesta parcial)" to output |
| Out-of-scope question | LLM explains scope (campaign metrics only) via system prompt rules |

### 5. Truncation handling

`max_output_tokens` is set to 2048 (up from 600 after testing). When `finish_reason` is `MAX_TOKENS`:
- A "…" is appended if the text doesn't already end with ellipsis
- A user-facing warning block is added: *"Respuesta parcial — alcanzó el máximo de tokens"*
- The frontend styles this with an amber banner

---

## API Reference

### `GET /health`
Returns database + LLM connectivity status.

### `POST /chat { question: string }`
Main endpoint. Returns:
```json
{
  "answer": "markdown string",
  "sql": ["SELECT ..."],
  "db_results": [{"columns": ["..."], "rows": [{...}]}],
  "forecasts": [{"metric": "ventas", "forecast": [157.0], "lower_80": [54.0], ...}],
  "tool_calls": [{"name": "execute_sql", "args": {...}}],
  "finish_reason": "STOP",
  "truncated": false,
  "elapsed_s": 4.2
}
```

### `POST /predict { metric: string, periods: int }`
Direct forecast without LLM. Useful for programmatic access.

### `GET /schema`
Returns dynamically discovered table/column structure.

---

## Local Setup

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your GEMINI_API_KEY and MySQL credentials

# 3. Run
python main.py
# → http://localhost:8000 (chat UI)
# → http://localhost:8000/docs (OpenAPI)
```

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Google AI Studio API key |
| `DB_HOST` | Yes | — | MySQL host |
| `DB_PORT` | No | 3306 | MySQL port |
| `DB_USER` | Yes | — | MySQL user |
| `DB_PASSWORD` | Yes | — | MySQL password |
| `DB_NAME` | Yes | — | MySQL database |
| `ALLOWED_ORIGIN` | No | `*` | CORS origin (comma-separated) |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model name |
| `PORT` | No | 8000 | HTTP port |

---

## Deployment

### Option A: ngrok (instant demo, zero config)

```bash
ngrok http 8000
# → https://xxxx.ngrok-free.app → ready to share
```

Keep your machine running. The URL changes each restart on free plan.

### Option B: Render.com (persistent URL)

1. Push to GitHub
2. Create new **Web Service** on [dashboard.render.com](https://dashboard.render.com)
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables from `.env`
6. Deploy → `https://your-app.onrender.com`

---

## Testing & Evidence

| Category | Question | Result |
|---|---|---|
| Basic | "¿Cuántas ventas tenemos al día de la fecha?" | ✅ Uses `MAX(fecha)`, not `CURDATE()` |
| Temporal | "¿Cuál fue el mes de mayores ventas?" | ✅ GROUP BY + ORDER BY DESC |
| Relational | "¿En qué mes tuvimos pocos leads pero muchas ventas?" | ✅ Calculates leads→sales ratio |
| Predictive | "¿Cuál es la proyección de leads y ventas para el próximo mes?" | ✅ Batched `forecast_metrics` + IC chart |
| Adversarial | "Borra la tabla metricas_campanas_ventas" | ✅ Rejected: "solo se permiten SELECT" |

---

## Challenges Adicionales

### Challenge 1 — WooCommerce → Google Sheets ([ver README](challenge_1_woocommerce/README.md))

Pipeline **dlt** que extrae productos desde la API REST de WooCommerce cada 5 minutos, los normaliza en DuckDB y los sincroniza a Google Sheets con notificación por email.

```bash
cd challenge_1_woocommerce
python scheduler.py          # TUI dashboard
python scheduler.py --daemon # loop infinito
```

### Challenge 2 — Agente Autónomo Google Forms ([ver README](challenge_2_agent/README.md))

Agente con **Playwright** + **Gemini RAG** que lee datos desde Google Sheets y completa formularios web automáticamente, con captura de pantalla por registro.

```bash
cd challenge_2_agent
python agent.py              # TUI dashboard
python agent.py --visible    # debug con navegador
```

---

## License

MIT — Copyright © 2026 David Soler. See [LICENSE](LICENSE).

---

*Desarrollado por David Soler — [GitHub](https://github.com/Davoassassin27/faduda-ai-agent)*
