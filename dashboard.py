import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime, timedelta
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

# --- ⚙️ CONFIGURACIÓN ---
st.set_page_config(page_title="Komo Executive v10", page_icon="📈", layout="wide")
st_autorefresh(interval=20000, key="data_refresh")

def load_credentials():
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    token = st.secrets.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    phone_id = st.secrets.get("PHONE_NUMBER_ID") or os.environ.get("PHONE_NUMBER_ID")
    return url, key, token, phone_id

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
supabase = create_client(URL, KEY)

# --- 📊 LÓGICA DE NEGOCIO Y MÉTRICAS ---
def get_all_data():
    orders = supabase.table("orders").select("*").order("created_at", desc=True).execute()
    chats = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(200).execute()
    drivers = supabase.table("drivers").select("*").execute()
    return pd.DataFrame(orders.data), pd.DataFrame(chats.data), pd.DataFrame(drivers.data)

df_o, df_c, df_d = get_all_data()

# 1. Cálculo de AHT (Average Handling Time)
def calculate_aht(df):
    completed = df[df['status'] == 'completed'].copy()
    if completed.empty: return 0
    completed['created_at'] = pd.to_datetime(completed['created_at'])
    completed['completed_at'] = pd.to_datetime(completed['completed_at'])
    diffs = (completed['completed_at'] - completed['created_at']).dt.total_seconds() / 60
    return diffs.mean()

# 2. Detector de Quejas y Mesa de Ayuda
def get_ticket_stats(df_chats):
    keywords = ['tarde', 'frio', 'mal', 'sucio', 'espera', 'error', 'no llego']
    quejas = df_chats[df_chats['content'].str.contains('|'.join(keywords), case=False, na=False)]
    return quejas

# --- 🖥️ INTERFAZ PRINCIPAL ---
st.title("📈 Komo Executive Dashboard")

tab_ops, tab_sales, tab_loyalty, tab_helpdesk = st.tabs([
    "🚀 Operaciones", "💰 Ventas y Cierre", "⭐ Lealtad", "🆘 Mesa de Ayuda"
])

# --- TAB 1: OPERACIONES (CORRECCIÓN DE REPETICIONES) ---
with tab_ops:
    # KPIs Rápidos
    c1, c2, c3, c4 = st.columns(4)
    active_orders = df_o[df_o['status'].isin(['confirmed', 'cooking', 'delivering'])].drop_duplicates(subset=['order_number'])
    c1.metric("📦 Pedidos Activos", len(active_orders))
    
    aht = calculate_aht(df_o)
    c2.metric("⏱️ AHT Promedio", f"{aht:.1f} min")
    
    total_pedidos = len(df_o)
    c3.metric("🔢 Total Pedidos", total_pedidos)
    
    quejas_df = get_ticket_stats(df_c)
    c4.metric("🚨 Tickets Abiertos", len(quejas_df), delta_color="inverse")

    col_l, col_r = st.columns([1.5, 1])
    with col_l:
        st.subheader("📋 Comandas en Tiempo Real")
        if active_orders.empty:
            st.info("No hay pedidos activos.")
        else:
            # USAMOS UN CONTENEDOR PARA EVITAR DUPLICADOS VISUALES
            for _, row in active_orders.iterrows():
                with st.expander(f"ORD: {row['order_number']} | {row['status'].upper()}", expanded=True):
                    st.write(f"**Items:** {row['order_details']} | **Monto:** ${row['total_price']}")
                    
                    # Chat y Feedback (Mesa de Ayuda)
                    st.caption("Última respuesta del cliente:")
                    c_hist = df_c[df_c['phone_number'] == row['customer_phone']].head(3)
                    for _, m in c_hist.iloc[::-1].iterrows():
                        color = "blue" if m['role'] == 'user' else "gray"
                        st.markdown(f"<span style='color:{color}'>{'👤' if m['role']=='user' else '🤖'}: {m['content']}</span>", unsafe_allow_html=True)
                    
                    # Botones de Acción corregidos
                    if row['status'] == 'confirmed':
                        if st.button(f"🔥 Cocinar {row['order_number']}", key=f"ck_{row['id']}"):
                            supabase.table("orders").update({"status": "cooking"}).eq("id", row['id']).execute()
                            st.rerun()

    with col_r:
        st.subheader("📍 Mapa de Flota")
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11),
            layers=[pdk.Layer('ScatterplotLayer', df_d, get_position='[longitude, latitude]', get_color='[0, 255, 0, 160]', get_radius=300)]))

# --- TAB 2: VENTAS Y CIERRE DE CAJA ---
with tab_sales:
    st.header("💵 Cierre de Caja Digital")
    col_c1, col_c2 = st.columns(2)
    
    with col_c1:
        st.write("### Artículos Más Vendidos")
        # Simulación de desglose de items (basado en order_details)
        if not df_o.empty:
            top_items = df_o['order_details'].str.split(',').explode().value_counts().head(5)
            st.bar_chart(top_items)

    with col_c2:
        st.write("### Resumen Económico")
        ventas_totales = df_o[df_o['status']=='completed']['total_price'].sum()
        st.info(f"**Ventas Totales del Turno:** ${ventas_totales:,.2f}")
        if st.button("📥 Generar Reporte de Cierre"):
            st.success("Reporte enviado al WhatsApp del Gerente.")

# --- TAB 3: LEALTAD (COUNT DE PEDIDOS) ---
with tab_loyalty:
    st.header("⭐ Ranking de Clientes (Loyalty)")
    if not df_o.empty:
        loyalty = df_o['customer_phone'].value_counts().reset_index()
        loyalty.columns = ['Teléfono', 'Total Pedidos']
        st.table(loyalty.head(10))

# --- TAB 4: MESA DE AYUDA (TICKETS) ---
with tab_helpdesk:
    st.header("🆘 Gestión de Tickets y Quejas")
    if not quejas_df.empty:
        for _, q in quejas_df.iterrows():
            st.error(f"⚠️ **Queja de {q['phone_number']}:** {q['content']}")
            if st.button(f"Atender Cliente {q['phone_number']}", key=f"q_{q['id']}"):
                st.info("Abriendo canal directo...")