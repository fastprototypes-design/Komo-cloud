from pydantic_settings import BaseSettings
from typing import Optional
import logging
import sys

class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_key: str
    
    # WhatsApp
    whatsapp_verify_token: str = "komo_secret_token_dev"
    whatsapp_access_token: Optional[str] = None
    
    # App
    app_env: str = "development"
    app_name: str = "Komo Operating System"
    app_version: str = "1.0"
    
    # Logging
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore" # Ignorar variables extra si las hay

# Instancia global
settings = Settings()

# Configuración de logs
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("komo")