import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Komo Manager", page_icon="🍕", layout="wide")

# --- 🔐 FUNCIÓN DE CONEXIÓN ROBUSTA (Plan A + Plan B) ---
def load_credentials():
    # INTENTO 1: La forma oficial de Streamlit (Para la Nube/Render)
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except FileNotFoundError:
        pass # Si falla, vamos al Plan B
        
    # INTENTO 2: Lectura Manual del archivo (Para tu Windows Local)
    # Buscamos el archivo donde el "Detective" dijo que estaba
    try:
        if os.path.exists(".streamlit/secrets.toml"):
            with open(".streamlit/secrets.toml", "r") as f:
                data = toml.load(f)
                return data["SUPABASE_URL"], data["SUPABASE_KEY"]
    except Exception as e:
        st.error(f"Error leyendo manual: {e}")
    
    return None, None

# Cargamos las claves
URL, KEY = load_credentials()

# Si después de los dos intentos no hay claves, detenemos todo.
if not URL or not KEY:
    st.error("❌ ERROR FATAL: No se pudieron leer las credenciales.")
    st.info("Verifica que el archivo .streamlit/secrets.toml tenga SUPABASE_URL y SUPABASE_KEY")
    st.stop()

# --- CONEXIÓN A SUPABASE ---
@st.cache_resource
def init_connection():
    return create_client(URL, KEY)

try:
    supabase = init_connection()
except Exception as e:
    st.error(f"Conexión fallida: {e}")
    st.stop()

# --- TÍTULO ---
st.title("🍕 Komo Enterprise - Torre de Control")
st.markdown("---")

# --- TRAER DATOS ---
def get_data():
    try:
        response = supabase.table("orders").select("*").order("created_at", desc=True).execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['created_at'] = pd.to_datetime(df['created_at'])
            df['total_price'] = pd.to_numeric(df['total_price'], errors='coerce').fillna(0)
            df['delivery_latitude'] = pd.to_numeric(df['delivery_latitude'], errors='coerce')
            df['delivery_longitude'] = pd.to_numeric(df['delivery_longitude'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error de base de datos: {e}")
        return pd.DataFrame()

if st.button('🔄 Actualizar Datos Ahora'):
    st.rerun()

df = get_data()

if not df.empty:
    today = datetime.now().date()
    # Filtro simple por fecha
    try:
        df_today = df[df['created_at'].dt.date == today]
    except:
        df_today = df # Si falla la fecha, muestra todo

    # KPI'S
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("💰 Ventas Hoy", f"${df_today['total_price'].sum():,.2f}")
    col2.metric("📦 Pedidos Hoy", len(df_today))
    gps_count = df_today['delivery_latitude'].notnull().sum()
    col4.metric("📍 Entregas GPS", f"{gps_count}")

    # MAPA
    st.subheader(f"🗺️ Mapa en Vivo")
    map_data = df.dropna(subset=['delivery_latitude', 'delivery_longitude'])
    
    if not map_data.empty:
        layer = pdk.Layer(
            "ScatterplotLayer",
            map_data,
            get_position='[delivery_longitude, delivery_latitude]',
            get_color='[200, 30, 0, 160]',
            get_radius=100,
            pickable=True,
        )
        view_state = pdk.ViewState(
            latitude=map_data['delivery_latitude'].iloc[0],
            longitude=map_data['delivery_longitude'].iloc[0],
            zoom=14
        )
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
    else:
        st.info("Sin datos de GPS aún.")

    # TABLA
    st.markdown("---")
    st.subheader("👨‍🍳 Comandas")
    st.dataframe(df[['created_at', 'order_details', 'total_price', 'status', 'delivery_address']], use_container_width=True)

else:
    st.warning("No hay datos para mostrar.")

# SIDEBAR
with st.sidebar:
    st.header("⚙️ Admin")
    st.success("🟢 Sistema Online")
    st.caption("Conectado vía Plan B (Local)" if ".streamlit" in str(URL) else "Conectado Seguro")