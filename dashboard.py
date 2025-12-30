import streamlit as st
import pandas as pd
from supabase import create_client
import pydeck as pdk
from datetime import datetime
import toml
import os
import time

# --- CONFIGURACIÓN INICIAL ---
st.set_page_config(page_title="Komo Ops Center", page_icon="⚡", layout="wide")

# --- 🔐 CONEXIÓN A SUPABASE ---
def load_credentials():
    try:
        # Intenta leer de los secretos de Streamlit Cloud
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except:
        try:
            # Intenta leer localmente
            if os.path.exists(".streamlit/secrets.toml"):
                with open(".streamlit/secrets.toml", "r") as f:
                    data = toml.load(f)
                    return data["SUPABASE_URL"], data["SUPABASE_KEY"]
        except: pass
    return None, None

URL, KEY = load_credentials()
if not URL:
    st.error("❌ No se encontraron credenciales. Revisa secrets.toml")
    st.stop()

# Cache para no reconectar a cada rato
@st.cache_resource
def init_connection():
    return create_client(URL, KEY)

supabase = init_connection()

# --- 🧠 FUNCIONES DE DATOS ---

def get_live_data():
    """Trae órdenes activas (Status: confirmed, cooking, delivering)"""
    try:
        response = supabase.table("orders").select("*").order("created_at", desc=True).limit(100).execute()
        df = pd.DataFrame(response.data)
        
        # Convertir columnas de tiempo
        cols_time = ['created_at', 'cooking_at', 'delivering_at', 'completed_at']
        for col in cols_time:
            if not df.empty and col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error leyendo órdenes: {e}")
        return pd.DataFrame()

def get_live_drivers():
    """Trae repartidores activos para el MAPA VERDE"""
    try:
        # Busca choferes disponibles
        response = supabase.table("drivers").select("*").eq("is_available", True).execute()
        return pd.DataFrame(response.data)
    except:
        return pd.DataFrame()

def get_complaints():
    """Trae tickets de soporte abiertos"""
    try:
        response = supabase.table("complaints").select("*").eq("status", "open").execute()
        return response.data
    except: return []

# --- FUNCIONES DE ACCIÓN (BOTONES) ---

def update_status_timestamp(order_id, new_status):
    """Actualiza el estado y guarda la hora exacta para los KPIs"""
    now = datetime.now().isoformat()
    update_data = {"status": new_status}
    
    # Guardamos timestamps para medir tiempos después
    if new_status == "cooking": update_data["cooking_at"] = now
    if new_status == "delivering": update_data["delivering_at"] = now
    if new_status == "completed": update_data["completed_at"] = now
    
    supabase.table("orders").update(update_data).eq("id", order_id).execute()
    st.toast(f"⏱️ Estado cambiado a: {new_status}")
    time.sleep(0.5)
    st.rerun()

def resolve_complaint(complaint_id):
    supabase.table("complaints").update({"status": "resolved"}).eq("id", complaint_id).execute()
    st.toast("Queja resuelta ✅")
    time.sleep(0.5)
    st.rerun()

# --- INTERFAZ GRÁFICA ---

st.title("⚡ Komo: Performance Dashboard")

# 1. SECCIÓN SUPERIOR: KPIs (Métricas de Tiempos y Ventas)
df = get_live_data()

if not df.empty and 'cooking_at' in df.columns:
    st.subheader("⏱️ Métricas de Eficiencia")
    
    # Cálculo de Tiempo de Preparación
    df_prep = df.dropna(subset=['cooking_at', 'delivering_at']).copy()
    avg_prep = (df_prep['delivering_at'] - df_prep['cooking_at']).dt.total_seconds().mean() / 60 if not df_prep.empty else 0

    # Cálculo de Tiempo de Entrega
    df_del = df.dropna(subset=['delivering_at', 'completed_at']).copy()
    avg_del = (df_del['completed_at'] - df_del['delivering_at']).dt.total_seconds().mean() / 60 if not df_del.empty else 0
        
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🔥 Tiempo Cocina", f"{avg_prep:.1f} min")
    k2.metric("🛵 Tiempo Entrega", f"{avg_del:.1f} min")
    
    # Ventas Totales (Aproximado)
    if 'total_price' in df.columns:
        # Asegurar que sea numérico
        df['total_price'] = pd.to_numeric(df['total_price'], errors='coerce').fillna(0)
        total_sales = df[df['status']=='completed']['total_price'].sum()
        k3.metric("💰 Ventas Cerradas", f"${total_sales:,.0f}")
    
    active_count = len(df[df['status'].isin(['confirmed', 'cooking', 'delivering'])])
    k4.metric("📦 En Proceso", active_count)
    st.divider()

# 2. SECCIÓN PRINCIPAL: MAPA Y PESTAÑAS
col_mapa, col_ops = st.columns([3, 2])

# --- COLUMNA IZQUIERDA: EL MAPA MULTICAPA ---
with col_mapa:
    st.subheader("📍 Radar de Operaciones")
    
    layers = []
    has_map_data = False
    
    # CAPA 1: PEDIDOS (Puntos ROJOS) 🔴
    if not df.empty:
        df_active = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])].copy()
        
        # Verificamos coordenadas
        if not df_active.empty and 'delivery_latitude' in df_active.columns:
            df_map_orders = df_active.dropna(subset=['delivery_latitude', 'delivery_longitude'])
            df_map_orders['lat'] = pd.to_numeric(df_map_orders['delivery_latitude'])
            df_map_orders['lon'] = pd.to_numeric(df_map_orders['delivery_longitude'])
            
            if not df_map_orders.empty:
                layer_orders = pdk.Layer(
                    "ScatterplotLayer",
                    df_map_orders,
                    get_position='[lon, lat]',
                    get_color='[200, 30, 0, 200]', # Rojo
                    get_radius=150,
                    pickable=True,
                    auto_highlight=True,
                )
                layers.append(layer_orders)
                has_map_data = True

    # CAPA 2: REPARTIDORES (Puntos VERDES) 🟢
    df_drivers = get_live_drivers()
    if not df_drivers.empty:
        # Intenta usar columnas latitude/longitude.
        # Si usas PostGIS directo, asegúrate de tener estas columnas en tu tabla drivers como float/numeric
        if 'latitude' in df_drivers.columns and 'longitude' in df_drivers.columns:
             df_drivers['lat'] = pd.to_numeric(df_drivers['latitude'])
             df_drivers['lon'] = pd.to_numeric(df_drivers['longitude'])
             df_drivers = df_drivers.dropna(subset=['lat', 'lon'])

             if not df_drivers.empty:
                layer_drivers = pdk.Layer(
                    "ScatterplotLayer",
                    df_drivers,
                    get_position='[lon, lat]',
                    get_color='[0, 255, 0, 200]', # Verde Neón
                    get_radius=150,
                    pickable=True,
                    auto_highlight=True,
                )
                layers.append(layer_drivers)
                has_map_data = True
                st.caption(f"🟢 {len(df_drivers)} Motos en línea")
        else:
            st.warning("⚠️ Tabla 'drivers' necesita columnas 'latitude' y 'longitude' para aparecer en el mapa.")

    # RENDERIZADO DEL MAPA
    view_state = pdk.ViewState(latitude=25.68, longitude=-100.31, zoom=12) # Mty por defecto
    
    # Si hay datos, centrar mapa en el primer punto
    if layers and has_map_data:
        # Intentar tomar coordenadas del primer pedido o primer driver para centrar
        try:
            if not df.empty and 'delivery_latitude' in df.columns:
                first = df.dropna(subset=['delivery_latitude']).iloc[0]
                view_state = pdk.ViewState(latitude=first['delivery_latitude'], longitude=first['delivery_longitude'], zoom=13)
        except: pass

    st.pydeck_chart(pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip={"text": "{status}\n{customer_phone}"}
    ))

# --- COLUMNA DERECHA: PESTAÑAS DE CONTROL ---
with col_ops:
    tab_pedidos, tab_quejas = st.tabs(["👨‍🍳 Gestión Pedidos", "🚨 Quejas & Evidencias"])
    
    # --- PESTAÑA 1: PEDIDOS ---
    with tab_pedidos:
        # Filtramos solo lo pendiente/activo
        active_orders = df[df['status'].isin(['confirmed', 'cooking', 'delivering'])] if not df.empty else pd.DataFrame()
        
        if not active_orders.empty:
            for index, row in active_orders.iterrows():
                # Estilo visual según estado
                color = "blue"
                emoji = "🆕"
                if row['status'] == 'cooking': 
                    color = "orange"
                    emoji = "🔥"
                if row['status'] == 'delivering': 
                    color = "green"
                    emoji = "🛵"
                
                with st.expander(f":{color}[{emoji} {row['status'].upper()}] - {str(row.get('customer_phone','Cte'))[-4:]}", expanded=True):
                    st.markdown(f"**Pedido:** {row.get('order_details', 'Sin detalles')}")
                    st.caption(f"📍 {row.get('delivery_address', 'Sin dirección')}")
                    
                    # Botonera de flujo
                    c1, c2 = st.columns([1, 1])
                    if row['status'] == 'confirmed':
                        if c2.button("A Cocina 🔥", key=f"c_{row['id']}"): update_status_timestamp(row['id'], "cooking")
                    elif row['status'] == 'cooking':
                        if c2.button("A Ruta 🛵", key=f"d_{row['id']}"): update_status_timestamp(row['id'], "delivering")
                    elif row['status'] == 'delivering':
                        if c2.button("Entregado ✅", key=f"f_{row['id']}"): update_status_timestamp(row['id'], "completed")
        else:
            st.success("🎉 Todo despachado. Esperando nuevos pedidos.")

    # --- PESTAÑA 2: QUEJAS ---
    with tab_quejas:
        complaints = get_complaints()
        if complaints:
            for t in complaints:
                with st.container(border=True):
                    st.error(f"Ticket #{str(t.get('id'))[:4]}")
                    st.write(f"**Cliente:** {t.get('customer_phone')}")
                    st.info(f"Reporte: {t.get('issue_description')}")
                    
                    # Mostrar foto si existe
                    if t.get('image_url'):
                        st.image(t['image_url'], caption="Evidencia", use_container_width=True)
                    
                    if st.button("Marcar Resuelto", key=f"res_{t['id']}"):
                        resolve_complaint(t['id'])
        else:
            st.info("No hay quejas pendientes. ¡Buen servicio! 🌟")

# Botón manual de refresco
if st.sidebar.button("🔄 REFRESCAR DATOS", type="primary"):
    st.rerun()