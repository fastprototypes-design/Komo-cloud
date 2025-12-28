import os
import httpx
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from openai import AsyncOpenAI
from config import settings, logger

# --- 🔐 VARIABLES DE ENTORNO ---
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KOMO_TOKEN_2025")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Clientes Globales
supabase: Client = None
openai_client: AsyncOpenAI = None
CURRENT_MENU_TEXT = "Cargando menú..."

# --- 🛠️ HERRAMIENTA 1: OBTENER MENÚ ---
async def get_menu_from_db():
    try:
        response = supabase.table("products").select("*").eq("is_active", True).execute()
        products = response.data
        if not products: return "Sin productos."
        
        menu_text = "MENÚ DISPONIBLE:\n"
        for p in products:
            menu_text += f"- {p['name']}: ${p['price']} ({p.get('category','')})\n"
        return menu_text
    except Exception:
        return "Error menú."

# --- 🛠️ HERRAMIENTA 2: GUARDAR PEDIDO (LAS MANOS DEL ROBOT) ---
async def registrar_pedido_db(phone: str, detalle: str, total: float):
    """Inserta el pedido en Supabase"""
    try:
        data = {
            "customer_phone": phone,
            "order_details": detalle,
            "total_price": total,
            "status": "confirmed"
        }
        # Insertar en la tabla 'orders'
        supabase.table("orders").insert(data).execute()
        logger.info(f"✅ PEDIDO GUARDADO: {detalle} para {phone}")
        return "Pedido registrado exitosamente en sistema."
    except Exception as e:
        logger.error(f"💥 Error guardando pedido: {e}")
        return "Error interno guardando pedido."

# --- 📡 ENVÍO WHATSAPP ---
async def send_whatsapp_message(to_number: str, text_body: str):
    if not WHATSAPP_TOKEN: return
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text_body},
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

# --- 🧠 LÓGICA DE IA (CON TOOLS) ---
async def ask_gpt4(user_message: str, user_phone: str):
    global CURRENT_MENU_TEXT
    
    # 1. Definimos la herramienta para que GPT la vea
    tools = [
        {
            "type": "function",
            "function": {
                "name": "registrar_pedido",
                "description": "Usa esto SOLO cuando el cliente confirme explícitamente que quiere hacer el pedido.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "detalle": {"type": "string", "description": "Resumen de productos, ej: 2 Pizzas Hawaianas"},
                        "total": {"type": "number", "description": "Precio total numérico, ej: 280"}
                    },
                    "required": ["detalle", "total"]
                }
            }
        }
    ]

    system_prompt = f"""
    Eres Komo, vendedor de pizzas.
    
    {CURRENT_MENU_TEXT}
    
    REGLAS:
    1. Si el cliente pregunta, responde con el menú.
    2. Si el cliente confirma la compra ("Sí, la quiero", "Mándamela"), LLAMA a la función 'registrar_pedido'.
    3. NO inventes que registraste el pedido si no llamas a la función.
    4. Sé breve.
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    try:
        # Primer intento: GPT piensa
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=messages,
            tools=tools,
            tool_choice="auto", 
            temperature=0.7
        )
        
        msg = response.choices[0].message
        
        # ¿GPT quiere usar la herramienta? (¿Quiere guardar pedido?)
        if msg.tool_calls:
            logger.info("🤖 GPT quiere registrar un pedido...")
            
            # Ejecutar la función Python real
            tool_call = msg.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            
            # Guardamos en Supabase
            resultado_db = await registrar_pedido_db(user_phone, args["detalle"], args["total"])
            
            # Le contamos a GPT que ya lo guardamos para que le avise al cliente
            messages.append(msg) # Agregamos la intención del asistente
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": resultado_db
            })
            
            # Segunda llamada: GPT genera la respuesta final al usuario
            final_response = await openai_client.chat.completions.create(
                model="gpt-4-turbo",
                messages=messages
            )
            return final_response.choices[0].message.content
        
        # Si no hubo venta, solo devuelve el texto normal
        return msg.content

    except Exception as e:
        logger.error(f"Error GPT: {e}")
        return "Un segundo, estoy revisando el inventario... ¿Qué decías?"

# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase, openai_client, CURRENT_MENU_TEXT
    supabase = create_client(settings.supabase_url, settings.supabase_key)
    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    CURRENT_MENU_TEXT = await get_menu_from_db()
    logger.info("🚀 KOMO VENTAJA: Sistema de Pedidos Activo")
    yield

app = FastAPI(title="Komo Orders", lifespan=lifespan)

# --- RUTAS ---
@app.get("/")
def home(): return {"status": "Taking Orders 📝"}

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/refresh-menu")
async def refresh_menu_manual():
    global CURRENT_MENU_TEXT
    CURRENT_MENU_TEXT = await get_menu_from_db()
    return {"status": "Menú actualizado"}

@app.get("/webhook")
async def verify(request: Request):
    if request.query_params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(request.query_params.get("hub.challenge"))
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        body = await request.json()
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if messages:
            msg = messages[0]
            phone = msg["from"]
            if msg["type"] == "text":
                text = msg["text"]["body"]
                # Pasamos el teléfono a ask_gpt4 para guardar el pedido
                resp = await ask_gpt4(text, phone)
                await send_whatsapp_message(phone, resp)

        return {"status": "ok"}
    except Exception:
        return {"status": "error"}