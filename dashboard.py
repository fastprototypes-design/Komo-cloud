import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os
import time
import requests
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Manager v8", page_icon="⚡", layout="wide")
st_autorefresh(interval=30000, key="data_refresh")

# --- CONEXIÓN ---
def load_credentials():
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"], st.secrets["WHATSAPP_TOKEN"], st.secrets["PHONE_NUMBER_ID"]
    except:
        try:
            if os.path.exists(".streamlit/secrets.toml"):
                with open(".streamlit/secrets.toml", "r") as f:
                    data = toml.load(f)
                    return data["SUPABASE_URL"], data["SUPABASE_KEY"], data.get("WHATSAPP_TOKEN"), data.get("PHONE_NUMBER_ID")
        except: pass
    return None, None, None, None

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
if not URL: st.stop()
supabase = create_client(URL, KEY)

# --- FUNCIONES ---
def send_whatsapp_template(to_phone, message):
    if not WA_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": message}}
    requests.post(url, headers=headers, json=data)

def get_live_data():
    response = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
    df = pd.DataFrame(response.data)
    cols = ['created_at', 'cooking_at', 'delivering_at', 'completed_at']
    for c in cols: 
        if not df.empty and c in df.columns: df[c] = pd.to_datetime(df[c], errors='coerce')
    return df

def get_drivers_list():
    try:
        # Traemos choferes activos
        response = supabase.table("drivers").select("name, phone_number").eq("is_available", True).execute()
        return response.data # Lista de diccionarios
    except: return []

# --- INTERFAZ ---
st.title("⚡ Komo: Centro de Mando v8")

tab_ops, tab_config = st.tabs(["🚀 Operación", "⚙️ Configuración"])

with tab_ops:
    df = get_live_data()
    active_orders = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])] if not df.empty else pd.DataFrame()
    
    if not active_orders.empty:
        # Pre-cargamos choferes para no consultar la DB en cada fila
        available_drivers = get_drivers_list()
        driver_options = {d['name']: d['phone_number'] for d in available_drivers} # Mapa Nombre -> Teléfono
        
        for idx, row in active_orders.iterrows():
            with st.expander(f"#{str(row.get('order_number'))[-4:]} | {row['status'].upper()}", expanded=True):
                st.write(f"**Pedido:** {row.get('order_details')}")
                st.info(f"📍 {row.get('delivery_address')} | 📞 {row.get('customer_phone')}")

                c1, c2 = st.columns(2)
                
                # 1. ESTADO: CONFIRMADO -> COCINA
                if row['status'] == 'confirmed':
                    if c1.button("🔥 A Cocina", key=f"c_{row['id']}"): 
                        supabase.table("orders").update({"status": "cooking", "cooking_at": datetime.now().isoformat()}).eq("id", row['id']).execute()
                        st.rerun()

                # 2. ESTADO: COCINA -> RUTA (AQUÍ ESTÁ LA MAGIA)
                elif row['status'] == 'cooking':
                    # Selector de Chofer
                    selected_driver_name = c1.selectbox("Asignar a:", options=list(driver_options.keys()), key=f"sel_{row['id']}")
                    
                    if c2.button("🛵 Enviar y Avisar Chofer", key=f"d_{row['id']}"):
                        driver_phone = driver_options[selected_driver_name]
                        
                        # A. Actualizamos DB (Status + Chofer asignado)
                        supabase.table("orders").update({
                            "status": "delivering", 
                            "delivering_at": datetime.now().isoformat(),
                            "driver_phone": driver_phone
                        }).eq("id", row['id']).execute()
                        
                        # B. Construimos mensaje para el chofer
                        msg_chofer = f"🛵 NUEVO VIAJE\n\n📦 Orden: {row.get('order_number')}\n📍 Dirección: {row.get('delivery_address')}\n📞 Cliente: {row.get('customer_phone')}\n📝 Detalle: {row.get('order_details')}\n💰 Cobrar: ${row.get('total_price')}\n\nAvísame cuando entregues."
                        
                        # C. Enviamos WhatsApp al Chofer
                        send_whatsapp_template(driver_phone, msg_chofer)
                        st.toast(f"Orden enviada a {selected_driver_name} 📤")
                        time.sleep(1)
                        st.rerun()

                # 3. ESTADO: EN RUTA (Espera cierre automático o manual)
                elif row['status'] == 'delivering':
                    st.caption(f"👀 Asignado a chofer: {row.get('driver_phone')}")
                    if c1.button("Forzar Cierre Manual ✅", key=f"f_{row['id']}"): 
                         supabase.table("orders").update({"status": "completed", "completed_at": datetime.now().isoformat()}).eq("id", row['id']).execute()
                         st.rerun()
    else:
        st.success("Todo tranquilo. 🍃")

with tab_config:
    st.info("Gestión de Menú y Choferes (Igual que v7)")
    # (Aquí va el código del editor de menú de la versión anterior)