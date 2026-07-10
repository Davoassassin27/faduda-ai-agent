"""
Streamlit page — Challenge 1: WooCommerce → Google Sheets Pipeline
"""
import sys
import os
import subprocess
from pathlib import Path

import streamlit as st

_ch1_dir = Path(__file__).resolve().parent.parent / "challenge_1_woocommerce"

# Remove all challenge paths from sys.path
sys.path = [p for p in sys.path if 'challenge' not in p.lower()]
# Remove all cached modules from challenges
for mod in list(sys.modules.keys()):
    if any(x in mod for x in ['config', 'dlt_pipeline', 'sheets_sync', 'email_notifier',
                               'challenge_1', 'challenge_2', 'cfg_c1', 'cfg_c2',
                               'dlt_pipeline_c1', 'sheets_sync_c1']):
        del sys.modules[mod]
sys.path.insert(0, str(_ch1_dir))

# cd to challenge dir so relative paths (service_account.json, DuckDB) work
_orig_cwd = os.getcwd()
os.chdir(str(_ch1_dir))

import config as cfg
import dlt_pipeline
import sheets_sync

os.chdir(_orig_cwd)

st.set_page_config(
    page_title="WooCommerce → Sheets",
    page_icon="📦",
    layout="wide",
)

st.title("📦 WooCommerce → Google Sheets")
st.markdown("Pipeline ETL con **dlt** que sincroniza productos cada 5 minutos.")

CONFIG_OK = True
try:
    app_cfg = cfg.AppConfig.load()
except Exception as e:
    CONFIG_OK = False
    cfg_error = str(e)

with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    if CONFIG_OK:
        st.success("Configuración cargada")
        st.code(
            f"WooCommerce URL: {app_cfg.wc.url}\n"
            f"Sheet ID: {app_cfg.gsheets.spreadsheet_id[:20]}...\n"
            f"Email: {app_cfg.email.notify_to}",
        )
    else:
        st.error(f"Error: {cfg_error}")
    st.divider()
    st.caption("Desarrollado por David Soler")

tab1, tab2 = st.tabs(["📊 Estado del Pipeline", "📋 Productos en Sheets"])

with tab1:
    st.markdown("## Estado del Pipeline")
    if CONFIG_OK:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Origen", "WooCommerce API")
        with col2:
            st.metric("Staging", "DuckDB")
        with col3:
            st.metric("Destino", "Google Sheets")
        with col4:
            st.metric("Intervalo", "5 min")

        if st.button("▶️ Ejecutar sincronización ahora", type="primary"):
            with st.status("Ejecutando pipeline...", expanded=True) as status:
                try:
                    st.write("📥 Ejecutando pipeline dlt...")
                    result = subprocess.run(
                        [sys.executable, str(_ch1_dir / "scheduler.py"), "--no-tui"],
                        capture_output=True, text=True, timeout=120, cwd=str(_ch1_dir),
                    )
                    out = result.stdout or ""
                    err = result.stderr or ""

                    # Parse summary from scheduler output
                    for line in out.splitlines():
                        if "Productos:" in line:
                            st.write(f"  {line.strip()}")
                        elif "Email" in line or "Tiempo" in line or "Errores" in line:
                            st.write(f"  {line.strip()}")

                    if result.returncode == 0:
                        status.update(label="✅ Completado", state="complete")
                    else:
                        # Mostrar últimos errores
                        err_lines = [l for l in err.splitlines() if 'Error' in l or 'error' in l or 'exception' in l]
                        for l in err_lines[-3:]:
                            st.error(l[:200])
                        status.update(label="⚠️ Completado con errores", state="error")

                    # Intentar leer productos (puede fallar por lock transitorio)
                    try:
                        os.chdir(str(_ch1_dir))
                        products2 = dlt_pipeline.get_loaded_products()
                        if products2:
                            st.success(f"{len(products2)} productos en DuckDB")
                    except Exception:
                        pass
                    finally:
                        os.chdir(_orig_cwd)
                except subprocess.TimeoutExpired:
                    st.error("Timeout (2 min)")
                    status.update(label="⏱️ Timeout", state="error")
                except Exception as e:
                    status.update(label="❌ Error", state="error")
                    st.error(str(e)[:200])
    else:
        st.warning("Verifica .env y service_account.json.")

with tab2:
    st.markdown("## Productos en Google Sheets")
    if CONFIG_OK:
        try:
            os.chdir(str(_ch1_dir))
            products = dlt_pipeline.get_loaded_products()
            if products:
                st.write(f"**{len(products)} productos** en DuckDB")
                import pandas as pd
                df = pd.DataFrame(products)
                for col in ["image_url", "permalink", "ingested_at"]:
                    if col in df.columns:
                        df[col] = df[col].astype(str)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No hay productos. Ejecuta el pipeline.")
            try:
                sc = sheets_sync.get_product_count(app_cfg.gsheets)
                st.metric("Productos en Sheets", sc)
            except Exception as e:
                st.warning(f"Sheets: {e}")
        except Exception as e:
            st.warning(f"Error: {e}")
        finally:
            os.chdir(_orig_cwd)
    else:
        st.info("Configura el pipeline.")
