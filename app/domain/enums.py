"""Enumerações de domínio para o Meu Assistente."""

from enum import StrEnum


class EventStatus(StrEnum):
    """Estado do ciclo de vida de um compromisso."""
    DRAFT = "draft"              # Proposta ainda pendente de confirmação ou dados
    CONFIRMED = "confirmed"      # Compromisso agendado e válido
    CANCELLED = "cancelled"      # Cancelado pelo usuário


class ParticipantStatus(StrEnum):
    """Estado de participação de uma pessoa em um evento."""
    PENDING = "pending"          # Convite ou presença a confirmar
    CONFIRMED = "confirmed"      # Confirmado
    DECLINED = "declined"        # Recusado / informou que não vai
    SUBSTITUTED = "substituted"  # Substituído por outra pessoa


class ParticipantRole(StrEnum):
    """Papel do participante no evento."""
    ORGANIZER = "organizer"
    ATTENDEE = "attendee"
    GUEST = "guest"


class IntentType(StrEnum):
    """Intenção detectada a partir da mensagem do usuário."""
    CREATE_EVENT = "create_event"
    QUERY_EVENTS = "query_events"
    UPDATE_EVENT = "update_event"
    CANCEL_EVENT = "cancel_event"
    ADD_PARTICIPANT = "add_participant"
    REPLACE_PARTICIPANT = "replace_participant"
    REMINDER_REQUEST = "reminder_request"
    UNKNOWN = "unknown"


class ConfidenceLevel(StrEnum):
    """Grau de certeza da extração semântica da IA."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionType(StrEnum):
    """Tipo de ação gravada na trilha de auditoria (change_log)."""
    CREATE = "create"
    UPDATE = "update"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    SUBSTITUTE = "substitute"


class SyncStatus(StrEnum):
    """Estado de sincronização com serviços externos (ex: Google Agenda)."""
    NOT_SYNCED = "not_synced"
    PENDING_SYNC = "pending_sync"
    SYNCED = "synced"
    FAILED = "failed"


class UserStatus(StrEnum):
    """Estado da conta do usuário."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"


class IdempotencyStatus(StrEnum):
    """Estado do processamento de uma mensagem para idempotência."""
    RECEIVED = "received"
    INTERPRETED = "interpreted"
    PROCESSED = "processed"
    CANCELLED_BY_USER = "cancelled_by_user"
    FAILED = "failed"


class ReminderStatus(StrEnum):
    """Estado de disparo de um lembrete programado."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"
