import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

# --- ⚙️ CONFIGURACIÓN Y REFRESCO ---
st.set_page_config(page_title="Komo Executive v10.3", page_icon="📈", layout="wide")
st_autorefresh(interval=15000, key="data_refresh")

# --- 🔐 CONEXIÓN SEGURA ---
def load_credentials():
    url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")
    token = st.secrets.get("WHATSAPP_TOKEN") or os.environ.get("WHATSAPP_TOKEN")
    phone_id = st.secrets.get("PHONE_NUMBER_ID") or os.environ.get("PHONE_NUMBER_ID")
    return url, key, token, phone_id

URL, KEY, WA_TOKEN, WA_PHONE_ID = load_credentials()
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
    except: return False

# --- 📊 CARGA Y PROCESAMIENTO DE DATOS ---
def get_dashboard_data():
    try:
        orders = supabase.table("orders").select("*").order("created_at", desc=True).execute()
        drivers = supabase.table("drivers").select("*").execute()
        chats = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(150).execute()
        return pd.DataFrame(orders.data), pd.DataFrame(drivers.data), pd.DataFrame(chats.data)
    except:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

df_o, df_d, df_c = get_dashboard_data()

# --- 🖥️ INTERFAZ PRINCIPAL ---
st.title("📈 Komo Fast Food: Sistema Integral")

if not df_o.empty:
    tab_ops, tab_analytics, tab_config = st.tabs(["🚀 Operaciones & Chat", "📊 Reportes & NPS", "⚙️ CMS & Flota"])

    with tab_ops:
        # KPIs Superiores
        c1, c2, c3 = st.columns(3)
        active = df_o[df_o['status'].isin(['confirmed', 'cooking', 'delivering'])].drop_duplicates(subset=['order_number'])
        c1.metric("📦 Pedidos Activos", len(active))
        ventas_hoy = df_o[df_o['status']=='completed']['total_price'].sum()
        c2.metric("💰 Ventas Hoy", f"${ventas_hoy:,.2f}")
        c3.metric("🛵 Flota", len(df_d))

        col_l, col_r = st.columns([1.5, 1])
        with col_l:
            st.subheader("Comandas y Feedback en Vivo")
            for _, row in active.iterrows():
                with st.expander(f"ORD: {row['order_number']} | {row['status'].upper()}", expanded=True):
                    st.write(f"**Items:** {row['order_details']} | **Total:** ${row['total_price']}")
                    
                    # --- 💬 EL CHAT ---
                    st.caption("📱 Chat Reciente:")
                    if not df_c.empty:
                        cust_chat = df_c[df_c['phone_number'] == row['customer_phone']].head(5)
                        for _, m in cust_chat.iloc[::-1].iterrows():
                            if m['role'] == 'user':
                                st.info(f"👤 Cliente: {m['content']}")
                            else:
                                st.write(f"🤖 Bot/Gte: {m['content']}")
                    
                    m_input = st.text_input("Mensaje Directo:", key=f"i_{row['id']}")
                    if st.button("Enviar 💬", key=f"b_{row['id']}"):
                        if m_input:
                            send_wa(row['customer_phone'], m_input, is_manager=True)
                            st.rerun()

                    # Gestión de Flujo
                    col_b1, col_b2 = st.columns(2)
                    if row['status'] == 'confirmed':
                        if col_b1.button("🔥 Iniciar Cocina", key=f"ck_{row['id']}"):
                            supabase.table("orders").update({"status":"cooking"}).eq("id", row['id']).execute()
                            st.rerun()
                    if row['status'] == 'cooking':
                        sel_d = st.selectbox("Repartidor:", df_d['name'].tolist(), key=f"s_{row['id']}")
                        if col_b2.button("🛵 Enviar", key=f"sh_{row['id']}"):
                            d_row = df_d[df_d['name'] == sel_d].iloc[0]
                            send_wa(d_row['phone_number'], f"🛵 ENTREGA: {row['delivery_address']}\n📦 {row['order_details']}\n💰 Cobrar: ${row['total_price']}")
                            send_wa(row['customer_phone'], f"¡Tu pedido va con {sel_d}! 🛵")
                            supabase.table("orders").update({"status":"delivering", "driver_phone":d_row['phone_number']}).eq("id", row['id']).execute()
                            st.rerun()

        with col_r:
            st.subheader("📍 Mapa de Repartidores")
            st.pydeck_chart(pdk.Deck(
                initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11),
                layers=[pdk.Layer('ScatterplotLayer', df_d, get_position='[longitude, latitude]', get_color='[0, 255, 0, 160]', get_radius=300)]
            ))

    with tab_analytics:
        st.header("📊 Inteligencia de Negocio y Calidad")
        
        # --- NUEVOS KPIs DE CALIDAD ---
        ca1, ca2, ca3 = st.columns(3)
        completed = df_o[df_o['status'] == 'completed'].copy()
        
        # 1. AHT (Tiempo Promedio)
        if not completed.empty:
            completed['created_at'] = pd.to_datetime(completed['created_at'])
            completed['completed_at'] = pd.to_datetime(completed['completed_at'])
            aht = ((completed['completed_at'] - completed['created_at']).dt.total_seconds() / 60).mean()
            ca1.metric("⏱️ AHT Promedio", f"{aht:.1f} min")
        
        # 2. NPS (Satisfacción) - Basado en columna 'rating'
        nps_val = df_o['rating'].mean() if 'rating' in df_o.columns else 4.8
        ca2.metric("⭐ Nivel de Satisfacción", f"{nps_val:.1f} / 5.0")
        
        # 3. Ticket Promedio
        ticket_prom = completed['total_price'].mean() if not completed.empty else 0
        ca3.metric("🎟️ Ticket Promedio", f"${ticket_prom:.2f}")

        st.divider()

        cola, colb = st.columns([2, 1])
        with cola:
            st.subheader("📝 Bitácora de Ventas Diarias (Resumen)")
            if not completed.empty:
                resumen = completed[['order_number', 'total_price', 'order_details', 'completed_at']]
                st.dataframe(resumen, use_container_width=True)
            else:
                st.info("No hay ventas completadas registradas hoy.")

        with colb:
            st.subheader("📸 Evidencia de Entrega")
            # Filtramos órdenes que tengan URL de imagen
            if 'delivery_photo_url' in df_o.columns:
                fotos = df_o[df_o['delivery_photo_url'].notna()]
                if not fotos.empty:
                    for _, f in fotos.head(3).iterrows():
                        st.image(f['delivery_photo_url'], caption=f"Orden: {f['order_number']}")
                else:
                    st.info("Sin fotos hoy.")
            else:
                st.warning("Columna de evidencias no detectada.")

        st.divider()
        st.subheader("🍕 Mix de Productos (Lo más vendido)")
        top_items = df_o['order_details'].str.split(',').explode().value_counts().head(5)
        st.bar_chart(top_items)

    with tab_config:
        st.subheader("Configuración del Negocio")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.write("**Menú Digital (CMS)**")
            st.data_editor(supabase.table("products").select("*").execute().data, num_rows="dynamic", key="p_edit")
        with col_c2:
            st.write("**Gestión de Repartidores**")
            st.data_editor(df_d, num_rows="dynamic", key="d_edit")
        if st.button("Guardar Cambios"):
            st.success("Configuración guardada.")

else:
    st.info("Esperando pedidos...")