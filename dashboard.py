import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os
import time

st.set_page_config(page_title="Komo Ops Center", page_icon="⚡", layout="wide")

# --- 🔐 CONEXIÓN ---
def load_credentials():
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except:
        try:
            if os.path.exists(".streamlit/secrets.toml"):
                with open(".streamlit/secrets.toml", "r") as f:
                    data = toml.load(f)
                    return data["SUPABASE_URL"], data["SUPABASE_KEY"]
        except: pass
    return None, None

URL, KEY = load_credentials()
if not URL: st.stop()
supabase = create_client(URL, KEY)

# --- 🧠 FUNCIONES AVANZADAS ---

def get_live_data():
    # Trae órdenes para cálculo de tiempos
    response = supabase.table("orders").select("*").order("created_at", desc=True).limit(100).execute()
    df = pd.DataFrame(response.data)
    
    # Convertir fechas
    cols_time = ['created_at', 'cooking_at', 'delivering_at', 'completed_at']
    for col in cols_time:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce') # Importante: coerce para evitar errores
            
    return df

def get_complaints():
    # Trae las quejas abiertas
    try:
        response = supabase.table("complaints").select("*").eq("status", "open").execute()
        return response.data
    except: return []

def update_status_timestamp(order_id, new_status):
    # CRONÓMETRO: Guardamos la hora exacta del clic
    now = datetime.now().isoformat()
    update_data = {"status": new_status}
    
    if new_status == "cooking": update_data["cooking_at"] = now
    if new_status == "delivering": update_data["delivering_at"] = now
    if new_status == "completed": update_data["completed_at"] = now
    
    supabase.table("orders").update(update_data).eq("id", order_id).execute()
    st.toast(f"⏱️ Estado actualizado a: {new_status}")
    time.sleep(0.5)
    st.rerun()

def resolve_complaint(complaint_id):
    supabase.table("complaints").update({"status": "resolved"}).eq("id", complaint_id).execute()
    st.toast("Queja resuelta ✅")
    time.sleep(0.5)
    st.rerun()

# --- INTERFAZ ---
st.title("⚡ Komo: Performance Dashboard")

# 1. KPIs DE TIEMPO (EL "AVERAGE HANDLE TIME")
df = get_live_data()
if not df.empty and 'cooking_at' in df.columns and 'completed_at' in df.columns:
    st.subheader("⏱️ Tiempos Promedio (AHT)")
    
    # Cálculo Prep Time (Cocina -> Entrega)
    # (Filtramos solo las que tienen ambos datos)
    df_prep = df.dropna(subset=['cooking_at', 'delivering_at']).copy()
    if not df_prep.empty:
        df_prep['mins_prep'] = (df_prep['delivering_at'] - df_prep['cooking_at']).dt.total_seconds() / 60
        avg_prep = df_prep['mins_prep'].mean()
    else:
        avg_prep = 0

    # Cálculo Delivery Time (Entrega -> Completado)
    df_del = df.dropna(subset=['delivering_at', 'completed_at']).copy()
    if not df_del.empty:
        df_del['mins_del'] = (df_del['completed_at'] - df_del['delivering_at']).dt.total_seconds() / 60
        avg_del = df_del['mins_del'].mean()
    else:
        avg_del = 0
        
    k1, k2, k3 = st.columns(3)
    k1.metric("🔥 Tiempo Cocina", f"{avg_prep:.1f} min", delta="-2 min" if avg_prep > 15 else "Ok")
    k2.metric("🛵 Tiempo Entrega", f"{avg_del:.1f} min", delta="-5 min" if avg_del > 30 else "Ok")
    k3.metric("📦 Total Órdenes", len(df))
    st.divider()

# 2. OPERACIÓN DIVIDIDA
col_ops, col_crm = st.columns([2, 1])

# --- COLUMNA IZQUIERDA: GESTIÓN DE PEDIDOS ---
with col_ops:
    st.subheader("👨‍🍳 Flujo de Pedidos")
    # Filtramos solo activos
    active_orders = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])]
    
    if not active_orders.empty:
        for index, row in active_orders.iterrows():
            color = "blue"
            if row['status'] == 'cooking': color = "orange"
            if row['status'] == 'delivering': color = "green"
            
            with st.expander(f":{color}[{row['status'].upper()}] - {row.get('customer_phone','Cte')[-4:]}", expanded=True):
                c1, c2 = st.columns([3,1])
                c1.write(f"**Pedido:** {row.get('order_details','')}")
                c1.caption(f"📍 {row.get('delivery_address','')}")
                
                # Botonera Lógica con Cronómetro
                if row['status'] == 'confirmed':
                    if c2.button("🔥 Cocinar", key=f"c_{row['id']}"): update_status_timestamp(row['id'], "cooking")
                elif row['status'] == 'cooking':
                    if c2.button("🛵 Enviar", key=f"d_{row['id']}"): update_status_timestamp(row['id'], "delivering")
                elif row['status'] == 'delivering':
                    if c2.button("✅ Fin", key=f"f_{row['id']}"): update_status_timestamp(row['id'], "completed")
    else:
        st.info("Cocina limpia ✨")

# --- COLUMNA DERECHA: QUEJAS Y ATENCIÓN ---
with col_crm:
    st.subheader("🚨 Quejas / Soporte")
    
    complaints = get_complaints()
    if complaints:
        for t in complaints:
            with st.container(border=True):
                st.error(f"Ticket #{str(t['id'])[:4]}")
                st.write(f"**Cliente:** {t['customer_phone']}")
                st.write(f"**Problema:** {t['issue_description']}")
                
                # MOSTRAR EVIDENCIA SI HAY
                if t.get('image_url'):
                    st.image(t['image_url'], caption="Evidencia enviada por WhatsApp", use_column_width=True)
                
                if st.button("Resolver Ticket", key=f"res_{t['id']}"):
                    resolve_complaint(t['id'])
    else:
        st.success("0 Quejas activas. ¡Excelente servicio! 🌟")

# Botón refresh
if st.sidebar.button("🔄 Refrescar Tablero"): st.rerun()