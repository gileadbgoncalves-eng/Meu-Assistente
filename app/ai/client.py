"""Cliente de interpretação semântica usando o SDK oficial google-genai."""

import json
from datetime import datetime
from typing import Optional, Protocol
import anyio

from google import genai
from google.genai import types

from app.config import settings
from app.ai.schemas import AIInterpretationResult
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.domain.enums import IntentType, ConfidenceLevel


class AIInterpreterProtocol(Protocol):
    """Protocolo abstrato para permitir injeção de dependência e mocks nos testes."""

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: Optional[str] = None,
    ) -> AIInterpretationResult:
        """Interpreta uma mensagem e devolve o schema estruturado."""
        ...


class GeminiInterpreter:
    """Implementação oficial da camada de IA com Google Gemini."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.api_key = api_key or settings.clean_gemini_api_key
        self.model_name = model_name or settings.GEMINI_MODEL
        self._client: Optional[genai.Client] = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY não foi configurada no ambiente ou no .env")
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: Optional[str] = None,
    ) -> AIInterpretationResult:
        """Envia mensagem ao Gemini solicitando retorno estruturado via response_schema."""
        prompt = build_user_prompt(
            raw_message=raw_message,
            anchor_datetime_iso=anchor_datetime.isoformat(),
            user_timezone=user_timezone,
            recent_context=recent_context,
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AIInterpretationResult,
            temperature=0.1,  # Baixa temperatura para determinismo na extração
        )

        candidate_models = [self.model_name]
        for fallback in ["gemini-3.5-flash-lite", "gemini-flash-latest"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        last_error = None
        for model in candidate_models:
            try:
                # Executa a chamada do SDK no thread pool para compatibilidade assíncrona
                response = await anyio.to_thread.run_sync(
                    lambda m=model: self.client.models.generate_content(
                        model=m,
                        contents=prompt,
                        config=config,
                    )
                )

                # Faz o parse validado via Pydantic
                raw_text = response.text or "{}"
                parsed_data = json.loads(raw_text)
                return AIInterpretationResult.model_validate(parsed_data)

            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                # Se for indisponibilidade temporária de alta demanda (503) ou modelo descontinuado (404), tenta o fallback
                if "503" in err_str or "404" in err_str or "UNAVAILABLE" in err_str:
                    continue
                # Se for outro erro (ex: credencial inválida), interrompe o loop
                break

        # Em caso de falha de conexão persistente em todos os modelos
        return AIInterpretationResult(
            intent=IntentType.UNKNOWN,
            confidence=ConfidenceLevel.LOW,
            uncertainty_reason=f"Falha na comunicação com o provedor de IA: {str(last_error)}",
            ambiguity_questions=["Não consegui processar a mensagem no momento. Poderia repetir?"],
        )
