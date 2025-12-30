import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import time
import requests
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Manager v8.5", page_icon="⚡", layout="wide")
st_autorefresh(interval=30000, key="data_refresh")

# --- CONEXIÓN ---
def load_credentials():
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"], st.secrets["WHATSAPP_TOKEN"], st.secrets["PHONE_NUMBER_ID"]
    except:
        return os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"), os.environ.get("WHATSAPP_TOKEN"), os.environ.get("PHONE_NUMBER_ID")

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
if not URL: st.stop()
supabase = create_client(URL, KEY)

# --- FUNCIONES ---
def send_whatsapp_manual(to_phone, message):
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": message}}
    requests.post(url, headers=headers, json=data)

def get_live_data():
    resp = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
    return pd.DataFrame(resp.data)

def get_drivers():
    resp = supabase.table("drivers").select("*").execute()
    return pd.DataFrame(resp.data)

# --- INTERFAZ ---
st.title("⚡ Komo: Centro de Mando")

tab_ops, tab_config = st.tabs(["🚀 Operación & Mapa", "⚙️ Configuración"])

with tab_ops:
    df = get_live_data()
    df_drivers = get_drivers()
    
    # 1. KPIs
    if not df.empty:
        k1, k2, k3 = st.columns(3)
        k1.metric("📦 Activos", len(df[df['status'].isin(['confirmed', 'cooking', 'delivering'])]))
        total = df[df['status']=='completed']['total_price'].sum() if 'total_price' in df.columns else 0
        k2.metric("💰 Ventas Hoy", f"${total:,.0f}")
        k3.metric("🛵 Choferes", len(df_drivers[df_drivers['is_available']==True]))

    # 2. MAPA (RECUPERADO)
    c_map, c_list = st.columns([2, 1])
    with c_map:
        layers = []
        # Capa Pedidos (Rojo)
        if not df.empty and 'delivery_latitude' in df.columns:
            orders_geo = df[df['status'].isin(['confirmed','cooking','delivering'])].dropna(subset=['delivery_latitude'])
            if not orders_geo.empty:
                layers.append(pdk.Layer("ScatterplotLayer", orders_geo, get_position='[delivery_longitude, delivery_latitude]', get_color='[200, 30, 0, 200]', get_radius=200))
        
        # Capa Choferes (Verde)
        if not df_drivers.empty and 'latitude' in df_drivers.columns:
            layers.append(pdk.Layer("ScatterplotLayer", df_drivers, get_position='[longitude, latitude]', get_color='[0, 200, 0, 200]', get_radius=150))
        
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11), layers=layers))

    # 3. LISTA DE PEDIDOS
    with c_list:
        if not df.empty:
            active = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])]
            for _, row in active.iterrows():
                with st.expander(f"ORD-{str(row['order_number'])[-4:]}"):
                    st.write(row['order_details'])
                    if "http" in str(row['delivery_address']):
                        st.markdown(f"📍 [Ver Ubicación]({row['delivery_address']})")
                    else: st.caption(row['delivery_address'])
                    
                    if row['status'] == 'cooking' and not df_drivers.empty:
                        driver_names = df_drivers['name'].tolist()
                        sel = st.selectbox("Asignar a:", driver_names, key=f"s_{row['id']}")
                        if st.button("Enviar 🛵", key=f"b_{row['id']}"):
                            d_phone = df_drivers[df_drivers['name']==sel]['phone_number'].values[0]
                            supabase.table("orders").update({"status":"delivering", "driver_phone": d_phone}).eq("id", row['id']).execute()
                            send_whatsapp_manual(d_phone, f"🛵 NUEVO VIAJE\nDestino: {row['delivery_address']}\nPedido: {row['order_details']}")
                            st.rerun()

with tab_config:
    st.subheader("📦 Gestión de Productos")
    prods = supabase.table("products").select("*").execute().data
    if prods:
        df_p = pd.DataFrame(prods)
        edited_p = st.data_editor(df_p, num_rows="dynamic", key="editor_prods")
        if st.button("Guardar Productos"):
            for r in edited_p.to_dict('records'):
                if pd.isna(r.get('id')): del r['id']
                supabase.table("products").upsert(r).execute()
            st.success("Menú actualizado")

    st.subheader("🛵 Gestión de Choferes")
    drivs = supabase.table("drivers").select("*").execute().data
    if drivs:
        df_d = pd.DataFrame(drivs)
        edited_d = st.data_editor(df_d, num_rows="dynamic", key="editor_drivers")
        if st.button("Guardar Choferes"):
            for r in edited_d.to_dict('records'):
                if pd.isna(r.get('id')): del r['id']
                supabase.table("drivers").upsert(r).execute()
            st.success("Choferes actualizados")