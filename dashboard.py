import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os
import time

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Komo Command Center", page_icon="🍕", layout="wide")

# --- 🔐 TU SISTEMA DE CONEXIÓN ---
def load_credentials():
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except FileNotFoundError:
        try:
            if os.path.exists(".streamlit/secrets.toml"):
                with open(".streamlit/secrets.toml", "r") as f:
                    data = toml.load(f)
                    return data["SUPABASE_URL"], data["SUPABASE_KEY"]
        except Exception: pass
    return None, None

URL, KEY = load_credentials()
if not URL or not KEY:
    st.error("❌ Sin credenciales. Revisa secrets.toml")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(URL, KEY)

supabase = init_connection()

# --- FUNCIONES DE DATOS ---
def get_live_orders():
    # Traemos órdenes activas
    response = supabase.table("orders").select("*").in_("status", ["confirmed", "cooking", "delivering"]).order("created_at", desc=True).execute()
    df = pd.DataFrame(response.data)
    if not df.empty:
        # Aseguramos que el precio sea numérico para sumar
        df['total_price'] = pd.to_numeric(df['total_price'], errors='coerce').fillna(0)
    return df

def get_completed_stats():
    # Para el resumen histórico (opcional, si quieres ver lo vendido hoy incluye lo cerrado)
    # Por ahora sumaremos solo lo activo + lo completado hoy si quisieras.
    # Usaremos el dataframe de live orders para el KPI rápido.
    pass 

def get_chat_preview():
    response = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(20).execute()
    return response.data

def update_order_status(order_id, new_status):
    supabase.table("orders").update({"status": new_status}).eq("id", order_id).execute()
    st.toast(f"Orden actualizada a: {new_status}")
    time.sleep(0.5)
    st.rerun()

# --- INTERFAZ ---
st.title("🛸 Komo: Operación Central")

# Layout: 60% Mapa, 40% Panel de Control (un poco más espacio para el chat)
col_mapa, col_ops = st.columns([3, 2])

df_orders = get_live_orders()

# --- COLUMNA IZQUIERDA: MAPA ---
with col_mapa:
    st.subheader("📍 Radar de Entregas")
    
    if not df_orders.empty:
        # KPI RÁPIDO MAPA
        active_count = len(df_orders)
        st.caption(f"Monitorizando {active_count} pedidos activos en el mapa.")

        map_data = df_orders.dropna(subset=['delivery_latitude', 'delivery_longitude'])
        map_data['delivery_latitude'] = pd.to_numeric(map_data['delivery_latitude'])
        map_data['delivery_longitude'] = pd.to_numeric(map_data['delivery_longitude'])

        if not map_data.empty:
            layer = pdk.Layer(
                "ScatterplotLayer",
                map_data,
                get_position='[delivery_longitude, delivery_latitude]',
                get_color='[200, 30, 0, 200]',
                get_radius=150,
                pickable=True,
                auto_highlight=True,
            )
            view_state = pdk.ViewState(
                latitude=map_data['delivery_latitude'].iloc[0],
                longitude=map_data['delivery_longitude'].iloc[0],
                zoom=13,
                pitch=45
            )
            r = pdk.Deck(
                layers=[layer],
                initial_view_state=view_state,
                tooltip={"text": "Orden: {order_number}\nStatus: {status}"}
            )
            st.pydeck_chart(r)
        else:
            st.info("Pedidos activos sin GPS compartido.")
    else:
        st.write("😴 Sin actividad en el radar.")
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=12)))

# --- COLUMNA DERECHA: SALA DE CONTROL ---
with col_ops:
    # --- SECCIÓN DE RESUMEN (KPIs) ---
    st.subheader("📊 Métricas en Vivo")
    
    if not df_orders.empty:
        total_ventas = df_orders['total_price'].sum()
        conteo_status = df_orders['status'].value_counts()
        
        # 1. Tarjetas de Totales
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("💰 Por Cobrar", f"${total_ventas:,.0f}")
        kpi2.metric("📦 Pedidos", len(df_orders))
        kpi3.metric("🛵 En Ruta", conteo_status.get('delivering', 0))
        
        # 2. Resumen de Acciones (Gráfico de Barras simple)
        st.markdown("**Estado del Flujo:**")
        st.bar_chart(conteo_status, color="#FF4B4B", height=150)
        
    else:
        st.metric("Esperando Ventas", "$0")

    st.divider()

    # --- PESTAÑAS OPERATIVAS ---
    tab_pedidos, tab_chat = st.tabs(["👨‍🍳 Cocina & Despacho", "💬 Live Chat"])
    
    with tab_pedidos:
        if not df_orders.empty:
            for index, row in df_orders.iterrows():
                # Color del borde según estado
                emoji_status = "🔥"
                if row['status'] == 'cooking': emoji_status = "👨‍🍳"
                if row['status'] == 'delivering': emoji_status = "🛵"

                with st.expander(f"{emoji_status} #{row.get('order_number','?')} | ${row['total_price']}", expanded=True):
                    st.write(f"**Detalle:** {row['order_details']}")
                    st.caption(f"📍 {row['delivery_address']}")
                    
                    # Botones de Acción
                    c1, c2, c3 = st.columns(3)
                    
                    # Lógica de Botones (Solo muestra el botón siguiente lógico)
                    if row['status'] == 'confirmed':
                        if c1.button("Cocinar", key=f"btn_c_{row['id']}"):
                            update_order_status(row['id'], "cooking")
                    elif row['status'] == 'cooking':
                        if c2.button("Enviar", key=f"btn_s_{row['id']}"):
                            update_order_status(row['id'], "delivering")
                    elif row['status'] == 'delivering':
                        if c3.button("Entregado", key=f"btn_d_{row['id']}"):
                            update_order_status(row['id'], "completed")
        else:
            st.info("Bandeja de entrada limpia. 🧹")

    with tab_chat:
        chats = get_chat_preview()
        if chats:
            for chat in chats:
                is_bot = chat['role'] == 'assistant'
                emoji = "🤖" if is_bot else "👤"
                
                # --- CORRECCIÓN DE COLORES ---
                # Usamos colores oscuros con texto blanco explícito
                bg_color = "#262730" if is_bot else "#1E4620" # Gris oscuro vs Verde oscuro
                align = "margin-right: 50px;" if not is_bot else "margin-left: 50px;"
                
                st.markdown(
                    f"""
                    <div style='
                        background-color: {bg_color};
                        color: white;
                        padding: 10px;
                        border-radius: 10px;
                        margin-bottom: 8px;
                        {align}
                    '>
                        <small style='opacity: 0.7;'>{emoji} {chat.get('phone_number','User')[-4:]}:</small><br>
                        {chat['content']}
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
        else:
            st.write("Sin historial reciente.")

# Botón de refresco manual
if st.sidebar.button("🔄 REFRESCAR", type="primary"):
    st.rerun()