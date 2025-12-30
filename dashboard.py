import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Super Manager v9.3", page_icon="📊", layout="wide")
# Refresco rápido para no perder respuestas del cliente
st_autorefresh(interval=15000, key="data_refresh")

# --- 🔐 CONEXIÓN ---
def load_credentials():
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    token = st.secrets.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    phone_id = st.secrets.get("PHONE_NUMBER_ID") or os.environ.get("PHONE_NUMBER_ID")
    return url, key, token, phone_id

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
supabase = create_client(URL, KEY)

def send_wa(to_phone, message, is_manager=False):
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    body = f"👨‍💼 (Gerente): {message}" if is_manager else message
    payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": body}}
    try:
        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 200:
            # GUARDAR EN DB: Importante para que aparezca en el historial del dashboard
            content = f"GERENTE DICE: {message}" if is_manager else message
            supabase.table("chat_history").insert({
                "phone_number": to_phone, "role": "assistant", "content": content
            }).execute()
            return True
    except: return False

# --- 📊 DATOS ---
orders_data = supabase.table("orders").select("*").order("created_at", desc=True).execute()
drivers_data = supabase.table("drivers").select("*").execute()
df_o = pd.DataFrame(orders_data.data)
df_d = pd.DataFrame(drivers_data.data)

# --- 🖥️ INTERFAZ ---
st.title("📊 Komo Fast Food: Centro de Mando")

# KPIs Restaurados
k1, k2, k3 = st.columns(3)
active = df_o[df_o['status'].isin(['confirmed', 'cooking', 'delivering'])]
k1.metric("📦 Pedidos Activos", len(active))
k2.metric("💰 Ventas Hoy", f"${df_o[df_o['status']=='completed']['total_price'].sum():,.0f}")
k3.metric("🛵 Repartidores", len(df_d))

col_l, col_r = st.columns([1.5, 1])

with col_l:
    st.subheader("📋 Comandas y Respuestas del Cliente")
    for _, row in active.iterrows():
        # Cambiamos el color o estilo si hay mensajes nuevos del cliente
        with st.expander(f"ORD: {row['order_number']} - {row['status'].upper()}", expanded=True):
            st.write(f"**Cliente:** {row['customer_phone']} | **Pedido:** {row['order_details']}")
            
            # --- 💬 EL CHAT EN VIVO (Aquí es donde el gerente lee al cliente) ---
            st.markdown("---")
            st.caption("📱 Conversación Reciente (Historial):")
            
            # Traemos los últimos 5 mensajes de este cliente específico
            chats = supabase.table("chat_history").select("*")\
                .eq("phone_number", row['customer_phone'])\
                .order("created_at", desc=True).limit(5).execute()
            
            if chats.data:
                for m in reversed(chats.data):
                    if m['role'] == 'user':
                        # Resaltamos el mensaje del cliente para que el