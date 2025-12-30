import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

# --- ⚙️ CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Komo Super Manager v9.4", page_icon="📊", layout="wide")
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
    st.error("⚠️ Faltan credenciales en Secrets.")
    st.stop()

supabase = create_client(URL, KEY)

# --- 🛠️ MOTOR DE MENSAJERÍA ---
def send_wa(to_phone, message, is_manager=False):
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    body = f"👨‍💼 (Gerente): {message}" if is_manager else message
    payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": body}}
    try:
        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 200:
            content = f"GERENTE DICE: {message}" if is_manager else message
            supabase.table("chat_history").insert({
                "phone_number": to_phone, "role": "assistant", "content": content
            }).execute()
            return True
    except Exception as e:
        st.error(f"Error WA: {e}")
        return False

# --- 📊 CARGA DE DATOS ---
def get_data():
    try:
        orders = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
        drivers = supabase.table("drivers").select("*").execute()
        chats = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(100).execute()
        return pd.DataFrame(orders.data), pd.DataFrame(drivers.data), pd.DataFrame(chats.data)
    except:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

df_o, df_d, df_c = get_data()

# --- 🖥️ INTERFAZ ---
st.title("📊 Komo Fast Food: Centro de Mando")

if not df_o.empty:
    # 📈 KPIs (HISTORIAL DE VENTAS Y ESTADO)
    k1, k2, k3 = st.columns(3)
    active = df_o[df_o['status'].isin(['confirmed', 'cooking', 'delivering'])]
    ventas_hoy = df_o[df_o['status']=='completed']['total_price'].sum()
    
    k1.metric("📦 Pedidos Activos", len(active))
    k2.metric("💰 Ventas Hoy", f"${ventas_hoy:,.2f}")
    k3.metric("🛵 Repartidores", len(df_d))

    tab_ops, tab_config = st.tabs(["🚀 Operaciones y Comandas", "⚙️ Configuración"])

    with tab_ops:
        col_l, col_r = st.columns([1.5, 1])
        
        with col_l:
            st.subheader("📋 Comandas y Feedback")
            for _, row in active.iterrows():
                with st.expander(f"ORD: {row['order_number']} - {row['status'].upper()}", expanded=True):
                    st.write(f"**Pedido:** {row['order_details']}")
                    st.write(f"**Dirección:** {row['delivery_address']}")
                    
                    # --- 💬 CHAT EN VIVO (Feedback del Cliente) ---
                    st.markdown("---")
                    st.caption("📱 Chat Reciente:")
                    if not df_c.empty:
                        customer_chat = df_c[df_c['phone_number'] == row['customer_phone']].head(5)
                        for _, m in customer_chat.iloc[::-1].iterrows():
                            if m['role'] == 'user':
                                st.info(f"👤 **Cliente:** {m['content']}")
                            else:
                                st.write(f"🤖 **Bot/Gerente:** {m['content']}")
                    
                    # Intervención
                    m_input = st.text_input("Responder:", key=f"in_{row['id']}")
                    if st.button("Enviar 💬", key=f"bt_{row['id']}"):
                        if m_input:
                            send_wa(row['customer_phone'], m_input, is_manager=True)
                            st.rerun()

                    # Flujo
                    if row['status'] == 'confirmed':
                        if st.button("🔥 Iniciar Cocina", key=f"ck_{row['id']}"):
                            supabase.table("orders").update({"status": "cooking"}).eq("id", row['id']).execute()
                            st.rerun()
                    
                    if row['status'] == 'cooking':
                        if not df_d.empty:
                            sel_d = st.selectbox("Asignar a:", df_d['name'].tolist(), key=f"s_{row['id']}")
                            if st.button("🛵 Enviar", key=f"sh_{row['id']}"):
                                d_row = df_d[df_d['name'] == sel_d].iloc[0]
                                send_wa(d_row['phone_number'], f"🛵 ENTREGA: {row['delivery_address']}\n📦 {row['order_details']}\n💰 Cobrar: ${row['total_price']}")
                                send_wa(row['customer_phone'], f"¡Tu pedido va con {sel_d}! 🛵")
                                supabase.table("orders").update({"status": "delivering", "driver_phone": d_row['phone_number']}).eq("id", row['id']).execute()
                                st.rerun()

        with col_r:
            st.subheader("📍 Mapa de Flota")
            if not df_d.empty:
                st.pydeck_chart(pdk.Deck(
                    initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11),
                    layers=[
                        pdk.Layer('ScatterplotLayer', df_d, get_position='[longitude, latitude]', 
                                  get_color='[0, 255, 0, 160]', get_radius=250)
                    ],
                ))

    with tab_config:
        st.subheader("Configuración")
        st.write("**Menú (CMS)**")
        p_data = supabase.table("products").select("*").execute().data
        st.data_editor(pd.DataFrame(p_data), num_rows="dynamic", key="p_edit")
        
        st.write("**Repartidores**")
        st.data_editor(df_d, num_rows="dynamic", key="d_edit")
        if st.button("Guardar Configuración"):
            st.success("Cambios guardados exitosamente.")

else:
    st.info("Esperando pedidos hoy...")