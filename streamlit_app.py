"""
streamlit_app.py — FADUA Interview Demo Dashboard
"""
import streamlit as st

st.set_page_config(
    page_title="FADUA — AI Agent Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.markdown(
    "<h1 style='text-align: center; color: #2563eb;'>⚡ FADUA</h1>",
    unsafe_allow_html=True,
)
st.sidebar.markdown("### AI Agent for Campaign Analytics")
st.sidebar.markdown("---")

st.sidebar.page_link("streamlit_app.py", label="🏠 Inicio")
st.sidebar.page_link(
    "pages/1_WooCommerce_Sheets.py",
    label="📦 WooCommerce → Sheets",
)
st.sidebar.page_link(
    "pages/2_Google_Forms_Agent.py",
    label="🤖 Google Forms Agent",
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "<p style='color: #94a3b8; font-size: 12px;'>"
    "Desarrollado por David Soler<br>"
    "MIT License 2026</p>",
    unsafe_allow_html=True,
)

st.title("FADUA — AI Agent for Campaign Analytics")
st.markdown(
    "**Conversational BI agent** that answers natural-language questions "
    "about advertising KPIs (Google Ads, Meta Ads, leads, sales, revenue) "
    "from a MySQL database, generates **SARIMAX forecasts** with confidence "
    "intervals, and renders interactive charts."
)

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("### 📦 Challenge 1")
    st.markdown(
        "**WooCommerce → Google Sheets**\n\n"
        "Pipeline ETL con **dlt** que extrae productos cada 5 minutos, "
        "los normaliza en DuckDB y los sincroniza a Google Sheets.\n\n"
        "• dlt verified source\n"
        "• DuckDB staging\n"
        "• gspread sync\n"
        "• SMTP email notification\n"
        "• Rich TUI dashboard"
    )
    st.page_link("pages/1_WooCommerce_Sheets.py", label="→ Abrir Dashboard")

with col2:
    st.markdown("### 🤖 Challenge 2")
    st.markdown(
        "**Google Forms Autonomous Agent**\n\n"
        "Agente con **Playwright** + **Gemini RAG** que lee datos desde "
        "Google Sheets y completa formularios web automáticamente.\n\n"
        "• RAG: retrieve → augment → generate\n"
        "• Multi-page navigation\n"
        "• Listbox/dropdown handling\n"
        "• Screenshot per record\n"
        "• Rich TUI dashboard"
    )
    st.page_link("pages/2_Google_Forms_Agent.py", label="→ Abrir Dashboard")

with col3:
    st.markdown("### 💬 MVP Chatbot")
    st.markdown(
        "**Text-to-SQL + Forecasts**\n\n"
        "Chat interface with **Gemini 2.5 Flash** function calling:\n\n"
        "• Natural-language → SQL\n"
        "• SARIMAX forecasts\n"
        "• Chart.js visualizations\n"
        "• Deployed on Render"
    )
    st.link_button(
        "→ Abrir Chat",
        "https://faduda-ai-agent.onrender.com",
    )

st.divider()
st.caption("Desarrollado por David Soler — MIT License 2026")
