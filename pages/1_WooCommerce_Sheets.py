"""
Streamlit page — Challenge 1: WooCommerce → Google Sheets Pipeline
"""
import sys
import os
from pathlib import Path

import streamlit as st

# Add challenge dir to path
_ch1_dir = Path(__file__).resolve().parent.parent / "challenge_1_woocommerce"
sys.path.insert(0, str(_ch1_dir))

st.set_page_config(
    page_title="WooCommerce → Sheets",
    page_icon="📦",
    layout="wide",
)

st.title("📦 WooCommerce → Google Sheets")
st.markdown("Pipeline ETL con **dlt** que sincroniza productos cada 5 minutos.")

# Import challenge modules
try:
    import config as cfg
    import dlt_pipeline
    import sheets_sync
    CONFIG_OK = True
except Exception as e:
    CONFIG_OK = False
    cfg_error = str(e)

with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    if CONFIG_OK:
        try:
            app_cfg = cfg.AppConfig.load()
            st.success("Configuración cargada")
            st.code(
                f"WooCommerce URL: {app_cfg.wc.url}\n"
                f"Sheet ID: {app_cfg.gsheets.spreadsheet_id[:20]}...\n"
                f"Email: {app_cfg.email.notify_to}",
            )
        except Exception as e:
            st.error(f"Error cargando config: {e}")
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
                    st.write("📥 Paso 1/3: Pipeline dlt (WooCommerce → DuckDB)")
                    load_info = dlt_pipeline.run_pipeline(app_cfg.wc)
                    st.write(f"  ✅ Pipeline completado")

                    st.write("📤 Paso 2/3: Sincronizando a Google Sheets")
                    products = dlt_pipeline.get_loaded_products()
                    if products:
                        rows = sheets_sync.sync_to_sheets(products, app_cfg.gsheets)
                        st.write(f"  ✅ {rows} productos sincronizados")
                    else:
                        st.warning("  ⚠️ Sin productos en DuckDB")

                    st.write("📧 Paso 3/3: Notificación por email")
                    prev = 0
                    try:
                        prev_file = Path(_ch1_dir) / ".last_count"
                        if prev_file.exists():
                            prev = int(prev_file.read_text().strip())
                    except: pass

                    from email_notifier import send_notification
                    import time
                    sent = send_notification(
                        products or [],
                        prev,
                        time.time() - 0,
                        app_cfg.email,
                    )
                    if sent:
                        st.write("  ✅ Email enviado")
                    else:
                        st.write("  ⏭️ SMTP no configurado")

                    status.update(label="✅ Sincronización completada", state="complete")
                    st.success(f"Pipeline ejecutado exitosamente. {len(products)} productos procesados.")
                except Exception as e:
                    status.update(label="❌ Error en pipeline", state="error")
                    st.error(f"Error: {e}")
    else:
        st.warning("No se pudo cargar la configuración. Verifica el .env y service_account.json.")

with tab2:
    st.markdown("## Productos en Google Sheets")

    if CONFIG_OK:
        try:
            prev = 0
            try:
                prev_file = Path(_ch1_dir) / ".last_count"
                if prev_file.exists():
                    prev = int(prev_file.read_text().strip())
            except: pass

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
                st.info("No hay productos cargados. Ejecuta el pipeline para poblar datos.")

            try:
                sheet_count = sheets_sync.get_product_count(app_cfg.gsheets)
                st.metric("Productos en Sheets", sheet_count)
            except Exception as e:
                st.warning(f"No se pudo leer Sheets: {e}")
        except Exception as e:
            st.warning(f"Error leyendo productos: {e}")
    else:
        st.info("Configura el pipeline para ver los productos.")
