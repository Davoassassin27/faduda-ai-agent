"""
Streamlit page — Challenge 2: Autonomous Google Forms Agent
"""
import sys
import os
import json
import glob
import subprocess
import time
from pathlib import Path

import streamlit as st

# Add challenge dir to path
_ch2_dir = Path(__file__).resolve().parent.parent / "challenge_2_agent"
sys.path.insert(0, str(_ch2_dir))

st.set_page_config(
    page_title="Google Forms Agent",
    page_icon="🤖",
    layout="wide",
)

st.title("🤖 Google Forms Autonomous Agent")
st.markdown(
    "Agente con **Playwright** + **Gemini RAG** que completa formularios "
    "automáticamente desde Google Sheets."
)

try:
    import config as cfg
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
                f"Form 1: {app_cfg.forms.form_1_url[:40]}...\n"
                f"Form 2: {app_cfg.forms.form_2_url[:40]}...\n"
                f"Sheet ID: {app_cfg.sheets.spreadsheet_id[:20]}...\n"
                f"Gemini: {'✓' if app_cfg.gemini.configured else '✗'}",
            )
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.error(f"Error: {cfg_error}")

    st.divider()
    st.caption("Desarrollado por David Soler")

tab1, tab2, tab3 = st.tabs([
    "📋 Datos del Sheet",
    "📝 Formularios",
    "📸 Capturas",
])

with tab1:
    st.markdown("## Datos desde Google Sheets")

    if CONFIG_OK:
        with st.spinner("Leyendo datos..."):
            try:
                from sheet_reader import SheetReader
                reader = SheetReader(app_cfg.sheets)
                data = reader.get_structured_data()

                for sheet in data.get("sheets", []):
                    with st.expander(f"**{sheet['title']}** — {sheet['row_count']} registros"):
                        st.markdown(f"**Columnas:** {', '.join(sheet['headers'])}")
                        if sheet.get("sample_rows"):
                            import pandas as pd
                            df = pd.DataFrame(
                                sheet["sample_rows"],
                                columns=sheet["headers"],
                            )
                            st.dataframe(df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.warning(f"No se pudo leer el sheet: {e}")
                st.info("Usando datos mock. Verifica service_account.json y .env")

        if st.button("🔄 Recargar datos"):
            st.rerun()
    else:
        st.warning("Configura el .env primero.")

with tab2:
    st.markdown("## Formularios")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Formulario 1 — Ventas")
        st.markdown(
            "Campos:\n"
            "- ID del Cliente\n"
            "- Nombre Completo\n"
            "- Correo Electrónico\n"
            "- Teléfono de Contacto\n"
            "- Modelo de Automóvil *(listbox)*\n"
            "- Valor Total del Vehículo\n"
            "- Tipo de Financiación\n\n"
            "4 páginas con navegación automática."
        )
        st.link_button("🔗 Abrir Form 1", app_cfg.forms.form_1_url)

    with col2:
        st.markdown("### Formulario 2 — Mora")
        st.markdown(
            "Campos:\n"
            "- ID de Cliente Asociado\n"
            "- Nombre del Cliente\n"
            "- Valor del Vehículo\n"
            "- Tipo Financiación\n"
            "- Estado de Cuenta Actual\n"
            "- Días de Atraso\n"
            "- Monto del Último Pago\n"
            "- Requiere Acción de Cobranza\n\n"
            "1 página, envío directo."
        )
        st.link_button("🔗 Abrir Form 2", app_cfg.forms.form_2_url)

    st.divider()

    st.markdown("### Ejecutar Agente")

    run_form = st.radio(
        "Seleccionar formulario",
        ["Ambos", "Form 1 (Ventas)", "Form 2 (Mora)"],
        horizontal=True,
    )

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        visible = st.checkbox("Mostrar navegador", value=False)
    with col2:
        dry_run = st.checkbox("Dry run", value=False)

    if st.button("▶️ Ejecutar Agente", type="primary"):
        form_map = {
            "Ambos": "",
            "Form 1 (Ventas)": "--form 1",
            "Form 2 (Mora)": "--form 2",
        }
        cmd = [
            sys.executable,
            str(_ch2_dir / "agent.py"),
            form_map[run_form],
            "--no-tui",
        ]
        if visible:
            cmd.append("--visible")
        if dry_run:
            cmd.append("--dry-run")
        cmd = [c for c in cmd if c]

        with st.status(f"Ejecutando agente: {' '.join(cmd)}", expanded=True) as status:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=str(_ch2_dir),
                )
                if result.stdout:
                    st.text(result.stdout[-3000:])
                if result.returncode == 0:
                    status.update(
                        label="✅ Agente completado exitosamente",
                        state="complete",
                    )
                else:
                    status.update(label="❌ Agente falló", state="error")
                    if result.stderr:
                        st.error(result.stderr[-1000:])
            except subprocess.TimeoutExpired:
                status.update(label="⏱️ Timeout (5 min)", state="error")
            except Exception as e:
                status.update(label=f"❌ Error: {e}", state="error")

with tab3:
    st.markdown("## Capturas de Pantalla")
    st.markdown("Cada formulario enviado genera una captura de auditoría.")

    screenshots_dir = _ch2_dir / "_screenshots"
    if screenshots_dir.exists():
        images = sorted(screenshots_dir.glob("*.png"))
        if images:
            cols = st.columns(3)
            for i, img_path in enumerate(images):
                with cols[i % 3]:
                    st.image(str(img_path), caption=img_path.name, use_container_width=True)
        else:
            st.info("No hay capturas. Ejecuta el agente para generar capturas.")
    else:
        st.info("Ejecuta el agente para generar capturas.")
