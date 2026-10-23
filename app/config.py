import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Diretório raiz absoluto do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
env_file_path = PROJECT_ROOT / ".env"

# Carrega explicitamente o .env da raiz com suporte a UTF-8 e UTF-8-sig (Windows Notepad)
if env_file_path.is_file():
    load_dotenv(dotenv_path=env_file_path, override=True, encoding="utf-8-sig")
else:
    found_env = find_dotenv(usecwd=True)
    if found_env:
        load_dotenv(dotenv_path=found_env, override=True, encoding="utf-8-sig")


class Settings(BaseSettings):
    """Configurações centrais do sistema carregadas de variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=str(env_file_path) if env_file_path.is_file() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identificação do projeto
    PROJECT_NAME: str = "Meu Assistente"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # API Gemini (Google GenAI)
    GEMINI_API_KEY: str = Field(default="", description="Chave da API do Google Gemini")
    GEMINI_MODEL: str = Field(default="gemini-3.5-flash", description="Modelo padrão do Gemini")

    @property
    def clean_gemini_api_key(self) -> str:
        """Retorna a chave da API do Gemini sem espaços, quebras ou aspas residuais."""
        key = (self.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()
        if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
            key = key[1:-1].strip()
        return key

    # Banco de Dados
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./meu_assistente.db",
        description="URL de conexão assíncrona do banco de dados (SQLite ou PostgreSQL)",
    )

    # Localização e Fuso Horário
    DEFAULT_TIMEZONE: str = Field(
        default="America/Sao_Paulo",
        description="Fuso horário de referência para cálculo de datas relativas",
    )
    DEFAULT_LOCALE: str = "pt_BR"

    # WhatsApp Cloud API (para integração com a Meta)
    WHATSAPP_VERIFY_TOKEN: str = "meu_assistente_token_verificacao"
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_API_VERSION: str = "v21.0"

    # Configurações do Agendador de Lembretes (Worker em Background)
    REMINDERS_ENABLED: bool = Field(
        default=True,
        description="Habilita ou desabilita o disparo de lembretes em background",
    )
    REMINDER_SWEEP_INTERVAL_SECONDS: int = Field(
        default=60,
        description="Intervalo em segundos entre cada varredura de lembretes pendentes",
    )

    # Futuras integrações (Google Agenda & Maps)
    GOOGLE_CALENDAR_CREDENTIALS_FILE: str = ""
    GOOGLE_MAPS_API_KEY: str = ""


settings = Settings()
