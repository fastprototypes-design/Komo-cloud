import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import requests
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Manager v8.9", page_icon="⚡", layout="wide")

# 🔄 Refresco automático para ver pedidos nuevos y mensajes
st_autorefresh(interval=15000, key="data_refresh")

# --- 🔐 CONEXIÓN ---
def load_credentials():
    # Intenta sacar de st.secrets (Streamlit Cloud) o os.environ (Local/Render)
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    token = st.secrets.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    phone_id = st.secrets.get("PHONE_NUMBER_ID") or os.environ.get("PHONE_NUMBER_ID")
    return url, key, token, phone_id

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()

if not URL or not KEY:
    st.error("⚠️ Error: Configura las variables de Supabase en los Secrets de Streamlit.")
    st.stop()

supabase = create_client(URL, KEY)

# --- 🛠️ MOTOR DE MENSAJERÍA ---

def send_wa(to_phone, message):
    """Función maestra de envío de WhatsApp desde el Dashboard"""
    if not WA_TOKEN or not WA_PHONE_ID:
        st.warning("⚠️ No se puede enviar WhatsApp: Faltan credenciales (Token/Phone ID).")
        return False
    
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
        return r.status_code == 200
    except:
        return False

# --- 📊 DATOS ---

def get_data():
    orders = supabase.table("orders").select("*").order("created_at", desc=True).limit(40).execute()
    drivers = supabase.table("drivers").select("*").execute()
    return pd.DataFrame(orders.data), pd.DataFrame(drivers.data)

# --- 🖥️ INTERFAZ ---

st.title("⚡ Komo Fast Food: Centro de Mando")

df_orders, df_drivers = get_data()

tab_ops, tab_config = st.tabs(["🚀 Operaciones", "⚙️ Configuración del Negocio"])

with tab_ops:
    if df_orders.empty:
        st.info("Esperando pedidos...")
    else:
        # KPIs
        c1, c2, c3 = st.columns(3)
        active_orders = df_orders[df_orders['status'].isin(['confirmed', 'cooking', 'delivering'])]
        c1.metric("📦 Pedidos Activos", len(active_orders))
        c2.metric("💰 Ventas Hoy", f"${df_orders[df_orders['status']=='completed']['total_price'].sum():,.2f}")
        c3.metric("🛵 Repartidores", len(df_drivers))

        col_list, col_map = st.columns([1, 1.5])

        with col_list:
            st.subheader("Gestión de Órdenes")
            for _, row in active_orders.iterrows():
                with st.expander(f"ORD: {row['order_number']} | {row['status'].upper()}", expanded=True):
                    st.write(f"**Cliente:** {row['customer_phone']}")
                    st.write(f"**Pedido:** {row['order_details']}")
                    st.write(f"**Dirección:** {row['delivery_address']}")
                    
                    # 1. Botón para pasar a Cocina
                    if row['status'] == 'confirmed':
                        if st.button(f"🔥 Empezar a Cocinar", key=f"cook_{row['id']}"):
                            supabase.table("orders").update({"status": "cooking"}).eq("id", row['id']).execute()
                            st.rerun()

                    # 2. Asignación a Repartidor (AQUÍ ESTÁ EL ARREGLO)
                    if row['status'] == 'cooking':
                        if not df_drivers.empty:
                            driver_names = df_drivers['name'].tolist()
                            sel_driver = st.selectbox("Seleccionar Repartidor:", driver_names, key=f"sel_{row['id']}")
                            
                            if st.button(f"🛵 Enviar con {sel_driver}", key=f"send_{row['id']}"):
                                driver_row = df_drivers[df_drivers['name'] == sel_driver].iloc[0]
                                d_phone = driver_row['phone_number']
                                
                                # Actualizar DB
                                supabase.table("orders").update({
                                    "status": "delivering", 
                                    "driver_phone": d_phone
                                }).eq("id", row['id']).execute()
                                
                                # MENSAJE AL REPARTIDOR (Link de Maps y Detalle)
                                msg_repartidor = (
                                    f"🛵 *NUEVA ENTREGA - KOMO FAST FOOD*\n\n"
                                    f"📍 *Destino:* {row['delivery_address']}\n"
                                    f"📦 *Pedido:* {row['order_details']}\n"
                                    f"💰 *Cobrar:* ${row['total_price']}\n\n"
                                    f"Favor de avisar al llegar."
                                )
                                send_wa(d_phone, msg_repartidor)
                                
                                # MENSAJE AL CLIENTE
                                msg_cliente = f"¡Buenas noticias! Tu pedido de Komo Fast Food ya va en camino con {sel_driver}. 🛵💨"
                                send_wa(row['customer_phone'], msg_cliente)
                                
                                st.success(f"Asignado a {sel_driver}")
                                time.sleep(1)
                                st.rerun()
                        else:
                            st.warning("No hay repartidores registrados.")

                    # 3. Chat Directo (Intervención del Gerente)
                    st.divider()
                    manager_msg = st.text_input("Mensaje rápido al cliente:", key=f"input_{row['id']}")
                    if st.button("Enviar Mensaje 💬", key=f"btn_{row['id']}"):
                        if manager_msg:
                            send_wa(row['customer_phone'], f"👨‍💼 (Gerente Komo): {manager_msg}")
                            st.toast("Enviado")

        with col_map:
            # Mapa de calor de pedidos
            st.subheader("Mapa de Entregas")
            # Filtrar solo los que tienen coordenadas
            map_df = df_orders[df_orders['status'].isin(['confirmed', 'cooking', 'delivering'])].copy()
            # Si la dirección contiene coordenadas del bot, podríamos extraerlas aquí
            st.pydeck_chart(pdk.Deck(
                initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11),
                layers=[
                    pdk.Layer(
                        'ScatterplotLayer',
                        df_drivers,
                        get_position='[longitude, latitude]',
                        get_color='[0, 255, 0, 160]',
                        get_radius=200,
                    ),
                ],
            ))

with tab_config:
    st.header("Configuración")
    # CMS de Productos
    st.subheader("Menú Digital")
    p_resp = supabase.table("products").select("*").execute()
    if p_resp.data:
        df_p = pd.DataFrame(p_resp.data)
        edited_df = st.data_editor(df_p, num_rows="dynamic")
        if st.button("Guardar Cambios en Menú"):
            for r in edited_df.to_dict('records'):
                if pd.isna(r.get('id')): del r['id']
                supabase.table("products").upsert(r).execute()
            st.success("Menú Actualizado")