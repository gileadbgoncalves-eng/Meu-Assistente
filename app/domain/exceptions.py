"""Exceções de domínio do Meu Assistente."""


class DomainError(Exception):
    """Exceção base para erros de regras de negócio."""
    def __init__(self, message: str, code: str = "DOMAIN_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class EntityNotFoundError(DomainError):
    """Lançada quando uma entidade requisitada não é encontrada."""
    def __init__(self, entity_name: str, entity_id: str | int):
        super().__init__(
            f"{entity_name} com identificador '{entity_id}' não foi encontrado.",
            code="ENTITY_NOT_FOUND"
        )


class EventConflictError(DomainError):
    """Lançada quando há conflito confirmado de horário na agenda."""
    def __init__(self, message: str, conflicting_event_id: str | int | None = None):
        super().__init__(message, code="EVENT_CONFLICT")
        self.conflicting_event_id = conflicting_event_id


class UnauthorizedAccessError(DomainError):
    """Lançada quando um usuário tenta acessar ou modificar dados sem permissão."""
    def __init__(self, message: str = "Acesso não autorizado aos recursos deste usuário."):
        super().__init__(message, code="UNAUTHORIZED_ACCESS")


class InvalidStateTransitionError(DomainError):
    """Lançada quando ocorre uma tentativa de transição inválida no ciclo de vida."""
    def __init__(self, current_state: str, target_state: str):
        super().__init__(
            f"Transição de estado inválida de '{current_state}' para '{target_state}'.",
            code="INVALID_STATE_TRANSITION"
        )


class IdempotencyDuplicateError(DomainError):
    """Lançada quando uma mensagem ou webhook com a mesma chave já foi processada."""
    def __init__(self, key: str):
        super().__init__(
            f"Mensagem duplicada detectada para a chave de idempotência '{key}'.",
            code="IDEMPOTENCY_DUPLICATE"
        )
