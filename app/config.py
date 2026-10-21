"""Configurações da aplicação via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Configurações centrais do sistema carregadas de variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identificação do projeto
    PROJECT_NAME: str = "Meu Assistente"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # API Gemini (Google GenAI)
    GEMINI_API_KEY: str = Field(default="", description="Chave da API do Google Gemini")
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Modelo padrão do Gemini")

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

    # Futuras integrações (Google Agenda & Maps)
    GOOGLE_CALENDAR_CREDENTIALS_FILE: str = ""
    GOOGLE_MAPS_API_KEY: str = ""


settings = Settings()
