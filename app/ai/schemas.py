"""Esquemas Pydantic v2 para extração semântica estruturada via Gemini."""


from pydantic import BaseModel, Field

from app.domain.enums import ConfidenceLevel, IntentType, ParticipantRole


class ExtractedParticipant(BaseModel):
    """Dados de participante extraídos da mensagem."""
    name: str = Field(description="Nome do participante")
    role: str = Field(
        default=ParticipantRole.ATTENDEE.value,
        description="Papel do participante: organizer, attendee ou guest",
    )
    phone_number: str | None = Field(
        default=None,
        description="Número de telefone informado, se houver",
    )
    replaces_name: str | None = Field(
        default=None,
        description="Nome de quem este participante está substituindo, se aplicável",
    )


class ProposedChange(BaseModel):
    """Mudança proposta em um compromisso existente."""
    field: str = Field(description="Nome do campo a ser alterado: title, start_time, end_time, location, participant")
    old_value: str | None = Field(default=None, description="Valor anterior inferido ou citado")
    new_value: str = Field(description="Novo valor proposto")


class AIInterpretationResult(BaseModel):
    """Resultado estruturado e tipado da interpretação semântica do Gemini."""

    intent: IntentType = Field(
        default=IntentType.UNKNOWN,
        description="Intenção principal da mensagem: create_event, query_events, update_event, cancel_event, add_participant, replace_participant, reminder_request, unknown",
    )
    title: str | None = Field(
        default=None,
        description="Título ou assunto do evento (ex: 'Consulta com Dentista', 'Reunião de Alinhamento')",
    )
    start_datetime: str | None = Field(
        default=None,
        description="Data e horário de início resolvidos no formato ISO 8601 (ex: '2026-10-22T14:00:00')",
    )
    end_datetime: str | None = Field(
        default=None,
        description="Data e horário de término resolvidos no formato ISO 8601, se informados ou calculáveis",
    )
    timezone: str = Field(
        default="America/Sao_Paulo",
        description="Fuso horário de referência",
    )
    location_raw: str | None = Field(
        default=None,
        description="Endereço físico ou local mencionado textualmente",
    )
    participants: list[ExtractedParticipant] = Field(
        default_factory=list,
        description="Lista de participantes identificados na mensagem",
    )
    event_reference: str | None = Field(
        default=None,
        description="Referência textual a evento existente ao alterar ou cancelar (ex: 'reunião da escola do Bernardo')",
    )
    extracted_facts: list[str] = Field(
        default_factory=list,
        description="Fatos textuais explícitos mencionados pelo usuário",
    )
    inferred_fields: list[str] = Field(
        default_factory=list,
        description="Campos deduzidos logicamente (ex: 'duração padrão de 1 hora inferida')",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Campos obrigatórios ausentes que impedem a conclusão direta (ex: 'horário', 'data')",
    )
    ambiguity_questions: list[str] = Field(
        default_factory=list,
        description="Perguntas curtas e diretas para esclarecer ambiguidades ou dados ausentes",
    )
    proposed_changes: list[ProposedChange] = Field(
        default_factory=list,
        description="Lista de modificações propostas em caso de alteração de evento",
    )
    confidence: ConfidenceLevel = Field(
        default=ConfidenceLevel.HIGH,
        description="Grau de certeza da interpretação: high, medium ou low",
    )
    uncertainty_reason: str | None = Field(
        default=None,
        description="Motivo da incerteza ou ambiguidade quando confidence for medium ou low",
    )
    is_confirmation_or_denial: str | None = Field(
        default=None,
        description="Indica se a mensagem é uma resposta direta a confirmação anterior: 'confirmed', 'denied' ou null",
    )

    # Subsistema de Lembretes (Reminders)
    reminder_minutes_before: int | None = Field(
        default=None,
        description="Minutos de antecedência do lembrete em relação ao início do compromisso (ex: 120 para 2h antes, 15 para 15min antes, 1440 para 1 dia antes)",
    )
    reminder_trigger_datetime: str | None = Field(
        default=None,
        description="Data e horário absoluto de disparo do lembrete no formato ISO 8601 (para lembretes avulsos ou calculados)",
    )
    reminder_message: str | None = Field(
        default=None,
        description="Descrição textual do lembrete (ex: 'Tomar remédio', 'Ligar para João')",
    )
    location: str | None = Field(
        default=None,
        description="Endereço físico ou local do evento",
    )

    def model_post_init(self, __context) -> None:
        if self.location is None and self.location_raw is not None:
            self.location = self.location_raw
        elif self.location_raw is None and self.location is not None:
            self.location_raw = self.location


class ExtractedEventData(BaseModel):
    """Dados de evento extraídos estruturadamente da mensagem."""
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    location_raw: str | None = None
    participants: list[str] = Field(default_factory=list)


class ExtractedReminderData(BaseModel):
    """Dados de lembrete extraídos estruturadamente."""
    message: str | None = None
    trigger_datetime: str | None = None
    minutes_before: int | None = None
    event_reference: str | None = None


ParsedIntent = AIInterpretationResult

