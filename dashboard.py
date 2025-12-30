import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
import os
import requests
import time
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Komo Super Manager v9.5", page_icon="📊", layout="wide")
st_autorefresh(interval=15000, key="data_refresh")

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
            content = f"GERENTE DICE: {message}" if is_manager else message
            supabase.table("chat_history").insert({"phone_number": to_phone, "role": "assistant", "content": content}).execute()
            return True
    except: return False

orders_data = supabase.table("orders").select("*").order("created_at", desc=True).execute()
drivers_data = supabase.table("drivers").select("*").execute()
chats_data = supabase.table("chat_history").select("*").order("created_at", desc=True).limit(100).execute()

df_o = pd.DataFrame(orders_data.data)
df_d = pd.DataFrame(drivers_data.data)
df_c = pd.DataFrame(chats_data.data)

st.title("📊 Komo Fast Food: Centro de Mando")

if not df_o.empty:
    k1, k2, k3 = st.columns(3)
    active = df_o[df_o['status'].isin(['confirmed', 'cooking', 'delivering'])]
    ventas = df_o[df_o['status']=='completed']['total_price'].sum()
    k1.metric("📦 Pedidos Activos", len(active))
    k2.metric("💰 Ventas Hoy", f"${ventas:,.2f}")
    k3.metric("🛵 Repartidores", len(df_d))

    col_l, col_r = st.columns([1.5, 1])
    with col_l:
        for _, row in active.iterrows():
            with st.expander(f"ORD: {row['order_number']} - {row['status'].upper()}", expanded=True):
                st.write(f"**Pedido:** {row['order_details']} | **Total:** ${row['total_price']}")
                st.write(f"**Dirección:** {row['delivery_address']}")
                
                if not df_c.empty:
                    st.caption("📱 Chat Reciente:")
                    customer_chat = df_c[df_c['phone_number'] == row['customer_phone']].head(5)
                    for _, m in customer_chat.iloc[::-1].iterrows():
                        if m['role'] == 'user':
                            st.info(f"👤 Cliente: {m['content']}")
                        else:
                            st.write(f"🤖 Bot: {m['content']}")
                
                m_input = st.text_input("Responder:", key=f"in_{row['id']}")
                if st.button("Enviar 💬", key=f"bt_{row['id']}"):
                    if m_input:
                        send_wa(row['customer_phone'], m_input, is_manager=True)
                        st.rerun()

                if row['status'] == 'confirmed':
                    if st.button("🔥 Cocinar", key=f"ck_{row['id']}"):
                        supabase.table("orders").update({"status": "cooking"}).eq("id", row['id']).execute()
                        st.rerun()
                
                if row['status'] == 'cooking':
                    sel_d = st.selectbox("Asignar repartidor:", df_d['name'].tolist(), key=f"s_{row['id']}")
                    if st.button("🛵 Enviar", key=f"sh_{row['id']}"):
                        d_row = df_d[df_d['name'] == sel_d].iloc[0]
                        send_wa(d_row['phone_number'], f"🛵 ENTREGA: {row['delivery_address']}\n📦 {row['order_details']}\n💰 Cobrar: ${row['total_price']}")
                        send_wa(row['customer_phone'], f"¡Tu pedido va con {sel_d}! 🛵")
                        supabase.table("orders").update({"status": "delivering", "driver_phone": d_row['phone_number']}).eq("id", row['id']).execute()
                        st.rerun()
    with col_r:
        st.subheader("📍 Mapa y Flota")
        st.pydeck_chart(pdk.Deck(initial_view_state=pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=11),
            layers=[pdk.Layer('ScatterplotLayer', df_d, get_position='[longitude, latitude]', get_color='[0, 255, 0, 160]', get_radius=250)]))