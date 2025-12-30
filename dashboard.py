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

# --- 🔐 TU SISTEMA DE CONEXIÓN ROBUSTO ---
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
    # Traemos órdenes que NO estén completadas o canceladas
    response = supabase.table("orders").select("*").in_("status", ["confirmed", "cooking", "delivering"]).order("created_at", desc=True).execute()
    return pd.DataFrame(response.data)

def get_chat_preview():
    # Traemos los últimos 20 mensajes globales para ver actividad
    response = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(20).execute()
    return response.data

def update_order_status(order_id, new_status):
    supabase.table("orders").update({"status": new_status}).eq("id", order_id).execute()
    st.toast(f"Orden actualizada a: {new_status}")
    time.sleep(1)
    st.rerun()

# --- INTERFAZ: COMMAND CENTER ---
st.title("🛸 Komo: Operación Central")

# Layout: 65% Mapa (Izquierda), 35% Operación (Derecha)
col_mapa, col_ops = st.columns([2, 1])

# --- COLUMNA IZQUIERDA: VISUALIZACIÓN GPS ---
with col_mapa:
    st.subheader("📍 Radar de Entregas")
    
    df_orders = get_live_orders()
    
    if not df_orders.empty:
        # Filtramos las que tienen GPS válido
        map_data = df_orders.dropna(subset=['delivery_latitude', 'delivery_longitude'])
        
        # Convertir a números por si acaso
        map_data['delivery_latitude'] = pd.to_numeric(map_data['delivery_latitude'])
        map_data['delivery_longitude'] = pd.to_numeric(map_data['delivery_longitude'])

        if not map_data.empty:
            # Capa de Puntos (Scatterplot)
            layer = pdk.Layer(
                "ScatterplotLayer",
                map_data,
                get_position='[delivery_longitude, delivery_latitude]',
                get_color='[200, 30, 0, 200]',
                get_radius=200,
                pickable=True,
                auto_highlight=True,
            )
            
            # Vista inicial centrada en el primer pedido o fija en Mty
            view_state = pdk.ViewState(
                latitude=map_data['delivery_latitude'].iloc[0],
                longitude=map_data['delivery_longitude'].iloc[0],
                zoom=13,
                pitch=45
            )
            
            # Render del mapa
            r = pdk.Deck(
                layers=[layer],
                initial_view_state=view_state,
                tooltip={"text": "Pedido: {order_number}\nCliente: {customer_phone}\nTotal: ${total_price}"}
            )
            st.pydeck_chart(r)
        else:
            st.info("Hay pedidos activos, pero sin GPS compartido.")
    else:
        st.write("😴 No hay pedidos activos en el radar.")
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=12)))

# --- COLUMNA DERECHA: SALA DE MÁQUINAS ---
with col_ops:
    tab_pedidos, tab_chat = st.tabs(["👨‍🍳 Cocina & Despacho", "💬 Live Chat"])
    
    # --- PESTAÑA 1: GESTIÓN DE PEDIDOS ---
    with tab_pedidos:
        if not df_orders.empty:
            for index, row in df_orders.iterrows():
                # Tarjeta de Pedido
                with st.expander(f"🔥 {row['order_number']} | {row['customer_phone']} | ${row['total_price']}", expanded=True):
                    st.markdown(f"**Detalle:** {row['order_details']}")
                    st.markdown(f"**Dirección:** {row['delivery_address']}")
                    st.caption(f"Status actual: {row['status'].upper()}")
                    
                    # Botones de Flujo de Trabajo
                    c1, c2, c3 = st.columns(3)
                    if row['status'] == 'confirmed':
                        if c1.button("👨‍🍳 Cocinar", key=f"cook_{row['id']}"):
                            update_order_status(row['id'], "cooking")
                    
                    if row['status'] == 'cooking':
                        if c2.button("🛵 Enviar", key=f"ship_{row['id']}"):
                            update_order_status(row['id'], "delivering")
                            
                    if row['status'] == 'delivering':
                        if c3.button("✅ Finalizar", key=f"done_{row['id']}"):
                            update_order_status(row['id'], "completed")
        else:
            st.success("Todo limpio. Esperando órdenes de WhatsApp... 📲")

    # --- PESTAÑA 2: MONITOR DE CHAT ---
    with tab_chat:
        st.caption("Últimos mensajes procesados por GPT-4")
        chats = get_chat_preview()
        if chats:
            for chat in chats:
                is_bot = chat['role'] == 'assistant'
                emoji = "🤖" if is_bot else "👤"
                align = "background-color: #f0f2f6;" if is_bot else "background-color: #dcf8c6;"
                
                # Burbuja de chat simple con HTML
                st.markdown(
                    f"""
                    <div style='padding:10px; border-radius:10px; margin-bottom:5px; {align}'>
                        <strong>{emoji} {chat.get('role', 'user')}:</strong><br>
                        {chat['content']}
                    </div>
                    """, 
                    unsafe_allow_html=True
                )
        else:
            st.warning("Historial vacío.")

# --- BOTÓN DE ACTUALIZACIÓN MANUAL ---
# (Idealmente, usaríamos st_autorefresh, pero esto es seguro y nativo)
if st.sidebar.button("🔄 REFRESCAR DATOS", type="primary"):
    st.rerun()

st.sidebar.info(f"Conexión: {URL[:15]}...")