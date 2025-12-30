import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Manager v8.9.1", page_icon="⚡", layout="wide")

# 🔄 Refresco automático para ver mensajes del cliente al instante
st_autorefresh(interval=15000, key="data_refresh")

# --- 🔐 CONEXIÓN ---
def load_credentials():
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    token = st.secrets.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    phone_id = st.secrets.get("PHONE_NUMBER_ID") or os.environ.get("PHONE_NUMBER_ID")
    return url, key, token, phone_id

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
if not URL: 
    st.error("⚠️ Error: Configura las variables en los Secrets de Streamlit.")
    st.stop()

supabase = create_client(URL, KEY)

# --- 🛠️ FUNCIONES DE MENSAJERÍA ---
def send_wa(to_phone, message):
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message}
    }
    try:
        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 200:
            # Guardamos en historial para que la IA sepa qué dijo el gerente
            supabase.table("chat_history").insert({
                "phone_number": to_phone,
                "role": "assistant",
                "content": f"GERENTE DICE: {message}"
            }).execute()
            return True
    except:
        return False

# --- 📊 OBTENCIÓN DE DATOS ---
def get_data():
    orders = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
    drivers = supabase.table("drivers").select("*").execute()
    return pd.DataFrame(orders.data), pd.DataFrame(drivers.data)

# --- 🖥️ INTERFAZ ---
st.title("⚡ Komo Fast Food: Centro de Mando")

df_orders, df_drivers = get_data()

tab_ops, tab_config = st.tabs(["🚀 Operaciones", "⚙️ Configuración"])

with tab_ops:
    if not df_orders.empty:
        # --- 📈 RESTAURACIÓN DE KPIs ---
        c1, c2, c3 = st.columns(3)
        active_mask = df_orders['status'].isin(['confirmed', 'cooking', 'delivering'])
        c1.metric("📦 Pedidos Activos", len(df_orders[active_mask]))
        ventas_hoy = df_orders[df_orders['status']=='completed']['total_price'].sum()
        c2.metric("💰 Ventas (Hoy)", f"${ventas_hoy:,.2f}")
        c3.metric("🛵 Flota Disponible", len(df_drivers[df_drivers['is_available']==True]))

        col_list, col_map = st.columns([1, 1.5])

        with col_list:
            st.subheader("Órdenes y Chat")
            active_orders = df_orders[active_mask]
            
            for _, row in active_orders.iterrows():
                with st.expander(f"ORD: {row['order_number']} | {row['status'].upper()}", expanded=True):
                    st.write(f"**Detalle:** {row['order_details']}")
                    st.write(f"**Dirección:** {row['delivery_address']}")
                    
                    # --- 💬 HISTORIAL DE COMANDAS (CHAT EN VIVO) ---
                    st.markdown("---")
                    st.caption("Últimos mensajes con el cliente:")
                    chats = supabase.table("chat_history").select("*")\
                        .eq("phone_number", row['customer_phone'])\
                        .order("created_at", desc=True).limit(3).execute()
                    
                    if chats.data:
                        for m in reversed(chats.data):
                            role_icon = "👤" if m['role'] == 'user' else "🤖"
                            st.write(f"{role_icon} {m['content']}")
                    
                    # Intervención del Gerente
                    manager_msg = st.text_input("Hablar con cliente:", key=f"in_{row['id']}")
                    if st.button("Enviar 💬", key=f"btn_{row['id']}"):
                        if manager_msg:
                            send_wa(row['customer_phone'], manager_msg)
                            st.toast("Mensaje enviado")
                            time.sleep(1)
                            st.rerun()

                    # Gestión de Status
                    if row['status'] == 'confirmed':
                        if st.button("🔥 Cocinar", key=f"cook_{row['id']}"):
                            supabase.table("orders").update({"status": "cooking"}).eq("id", row['id']).execute()
                            st.rerun()

                    if row['status'] == 'cooking':
                        driver_list = df_drivers['name'].tolist()
                        sel_drv = st.selectbox("Asignar a:", driver_list, key=f"drv_{row['id']}")
                        if st.button("🛵 Enviar Pedido", key=f"send_{row['id']}"):
                            d_row = df_drivers[df_drivers['name'] == sel_drv].iloc[0]
                            # Notificar a Repartidor y Cliente
                            msg_rep = f"🛵 ENTREGA: {row['delivery_address']}\n📦 {row['order_details']}\n💰 Cobrar: ${row['total_price']}"
                            send_wa(d_row['phone_number'], msg_rep)
                            send_wa(row['customer_phone'], f"¡Tu pedido ya va en camino con {sel_drv}! 🛵")
                            
                            supabase.table("orders").update({
                                "status": "delivering", 
                                "driver_phone": d_row['phone_number']
                            }).eq("id", row['id']).execute()
                            st.rerun()

        with col_map:
            st.subheader("Mapa de Operación")
            st.pydeck_chart(pdk.Deck(
                initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=12),
                layers=[
                    pdk.Layer('ScatterplotLayer', df_drivers, get_position='[longitude, latitude]', 
                              get_color='[0, 255, 0, 160]', get_radius=200)
                ],
            ))

with tab_config:
    st.subheader("Menú y Precios")
    p_data = supabase.table("products").select("*").execute().data
    if p_data:
        df_p = pd.DataFrame(p_data)
        edited = st.data_editor(df_p, num_rows="dynamic")
        if st.button("Actualizar Todo"):
            for r in edited.to_dict('records'):
                if pd.isna(r.get('id')): del r['id']
                supabase.table("products").upsert(r).execute()
            st.success("¡Menú Guardado!")