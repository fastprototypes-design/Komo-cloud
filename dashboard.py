import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os
import time
import requests # Necesario para enviar WhatsApp desde aquí

st.set_page_config(page_title="Komo Manager", page_icon="👔", layout="wide")

# --- 🔐 CONEXIÓN ---
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

# --- 📨 FUNCIÓN PARA ENVIAR WHATSAPP MANUAL ---
def send_whatsapp_manual(to_phone, message):
    if not WA_TOKEN or not WA_PHONE_ID:
        st.error("Faltan credenciales de WhatsApp en secrets.toml")
        return
    
    url = f"https://graph.facebook.com/v17.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": f"👨‍💼 Gerente: {message}"} # Prefijo para que sepan que es humano
    }
    try:
        r = requests.post(url, headers=headers, json=data)
        if r.status_code == 200:
            st.toast(f"Mensaje enviado a {to_phone} 📤")
            # Guardamos en el historial para que GPT sepa que intervenimos
            supabase.table("chat_history").insert({
                "phone_number": to_phone,
                "role": "assistant", 
                "content": f"[INTERVENCIÓN MANUAL]: {message}"
            }).execute()
        else:
            st.error(f"Error WhatsApp: {r.text}")
    except Exception as e:
        st.error(f"Error de conexión: {e}")

# --- 🧠 FUNCIONES DE DATOS ---
def get_live_data():
    response = supabase.table("orders").select("*").order("created_at", desc=True).limit(50).execute()
    df = pd.DataFrame(response.data)
    # Limpieza básica
    cols = ['created_at', 'cooking_at', 'delivering_at', 'completed_at']
    for c in cols: 
        if not df.empty and c in df.columns: df[c] = pd.to_datetime(df[c], errors='coerce')
    return df

def get_products():
    response = supabase.table("products").select("*").order("name").execute()
    return pd.DataFrame(response.data)

def get_drivers():
    response = supabase.table("drivers").select("*").execute()
    return pd.DataFrame(response.data)

def update_status_timestamp(order_id, new_status):
    now = datetime.now().isoformat()
    update_data = {"status": new_status}
    if new_status == "cooking": update_data["cooking_at"] = now
    if new_status == "delivering": update_data["delivering_at"] = now
    if new_status == "completed": update_data["completed_at"] = now
    supabase.table("orders").update(update_data).eq("id", order_id).execute()
    st.toast(f"Status: {new_status}")
    time.sleep(0.5); st.rerun()

# --- INTERFAZ ---
st.title("👔 Komo: Panel de Gerencia")

tab_ops, tab_config = st.tabs(["🚀 Operación Diaria", "⚙️ Configuración (Menú/Choferes)"])

# === PESTAÑA 1: OPERACIÓN ===
with tab_ops:
    col_map, col_list = st.columns([2, 1])
    df = get_live_data()
    
    # MAPA (Resumido para brevedad, usa el código de v5.0 si quieres el full multicapa)
    with col_map:
        if not df.empty:
            active = df[df['status'].isin(['confirmed','cooking','delivering'])].dropna(subset=['delivery_latitude'])
            if not active.empty:
                st.pydeck_chart(pdk.Deck(
                    map_style=None,
                    initial_view_state=pdk.ViewState(latitude=active['delivery_latitude'].iloc[0], longitude=active['delivery_longitude'].iloc[0], zoom=12),
                    layers=[pdk.Layer("ScatterplotLayer", active, get_position='[delivery_longitude, delivery_latitude]', get_color='[200, 30, 0, 160]', get_radius=200)]
                ))
            else: st.info("Mapa limpio.")
    
    # LISTA DE PEDIDOS + CHAT MANUAL
    with col_list:
        active_orders = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])] if not df.empty else pd.DataFrame()
        if not active_orders.empty:
            for idx, row in active_orders.iterrows():
                with st.expander(f"#{str(row['order_number'])[-4:]} | {row['status'].upper()}", expanded=True):
                    st.write(f"**{row.get('order_details')}**")
                    st.caption(f"📍 {row.get('delivery_address')}")
                    
                    # BOTONES DE ESTADO
                    c1, c2 = st.columns(2)
                    if row['status'] == 'confirmed':
                        if c1.button("Cocinar 🔥", key=f"c_{row['id']}"): update_status_timestamp(row['id'], "cooking")
                    elif row['status'] == 'cooking':
                        if c1.button("Enviar 🛵", key=f"d_{row['id']}"): update_status_timestamp(row['id'], "delivering")
                    elif row['status'] == 'delivering':
                        if c1.button("Fin ✅", key=f"f_{row['id']}"): update_status_timestamp(row['id'], "completed")
                    
                    # 💬 CHAT MANUAL AL CLIENTE
                    st.markdown("---")
                    msg_text = st.text_input("Mensaje al cliente:", placeholder="Ej: Ya salimos...", key=f"txt_{row['id']}")
                    if st.button("Enviar WhatsApp 📤", key=f"snd_{row['id']}"):
                        if msg_text:
                            send_whatsapp_manual(row['customer_phone'], msg_text)
                        else:
                            st.warning("Escribe algo primero.")

# === PESTAÑA 2: CONFIGURACIÓN (CMS) ===
with tab_config:
    st.header("🛠️ Panel de Administración")
    
    col_menu, col_drivers = st.columns(2)
    
    # 1. EDITOR DE MENÚ
    with col_menu:
        st.subheader("🍕 Menú y Precios")
        df_prods = get_products()
        
        # WIDGET PODEROSO: Data Editor (Como Excel)
        edited_prods = st.data_editor(
            df_prods,
            num_rows="dynamic", # Permite agregar/borrar filas
            column_config={
                "price": st.column_config.NumberColumn("Precio", format="$%d"),
                "is_active": st.column_config.CheckboxColumn("Disponible"),
            },
            key="editor_menu"
        )
        
        if st.button("💾 Guardar Cambios Menú"):
            # Lógica simplificada: Upsert (Insertar o Actualizar)
            # Convertimos a dict para subir a supabase
            try:
                # Nota: data_editor retorna el DF modificado.
                # Para sincronizar con Supabase, lo más fácil es iterar y upsert
                # (En producción se hace comparando cambios, aquí guardamos todo por simplicidad)
                records = edited_prods.to_dict('records')
                for record in records:
                    # Limpiamos NaN
                    if pd.isna(record.get('id')): del record['id'] # Es nuevo
                    supabase.table("products").upsert(record).execute()
                st.success("Menú actualizado en la nube ☁️")
            except Exception as e:
                st.error(f"Error guardando: {e}")

    # 2. EDITOR DE CHOFERES
    with col_drivers:
        st.subheader("🏍️ Flota de Choferes")
        df_drivers = get_drivers()
        
        edited_drivers = st.data_editor(
            df_drivers,
            num_rows="dynamic",
            column_config={
                "phone_number": st.column_config.TextColumn("WhatsApp (521...)", help="Sin espacios"),
                "is_available": st.column_config.CheckboxColumn("Activo"),
            },
            key="editor_drivers"
        )
        
        if st.button("💾 Guardar Choferes"):
            try:
                records = edited_drivers.to_dict('records')
                for record in records:
                    if pd.isna(record.get('id')): del record['id']
                    supabase.table("drivers").upsert(record).execute()
                st.success("Lista de choferes actualizada ☁️")
            except Exception as e:
                st.error(f"Error guardando: {e}")