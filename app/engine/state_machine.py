"""Máquina de estados para gerenciamento de propostas pendentes e confirmações."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.enums import ActionType


class ResponseDisposition(StrEnum):
    """Classificação da resposta do usuário frente a uma ação pendente."""
    CONFIRMED = "confirmed"     # Usuário aprovou a ação explicitamente ("sim", "pode agendar")
    DENIED = "denied"           # Usuário recusou a ação ("não", "cancela", "deixa quieto")
    NEW_INPUT = "new_input"     # Usuário enviou nova instrução ou dado adicional
    NO_PENDING = "no_pending"   # Não havia proposta pendente de confirmação


class PendingProposal(BaseModel):
    """Proposta mantida em memória aguardando confirmação do usuário."""
    user_id: int
    action_type: ActionType
    target_event_id: int | None = None
    data: dict = Field(default_factory=dict)
    summary: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationStateManager:
    """Gerenciador de estado de conversas, histórico recente e confirmações ativas por usuário."""

    def __init__(self):
        # Armazena em memória as propostas pendentes indexadas por user_id
        self._pending_proposals: dict[int, PendingProposal] = {}
        # Histórico recente de mensagens por usuário para resolução anafórica
        self._history: dict[int, list[dict[str, str]]] = {}
        # Último compromisso criado ou manipulado na sessão
        self._last_event_ids: dict[int, int] = {}

    def set_last_event_id(self, user_id: int, event_id: int) -> None:
        """Registra o último compromisso criado ou manipulado na sessão."""
        self._last_event_ids[user_id] = event_id

    def get_last_event_id(self, user_id: int) -> int | None:
        """Recupera o ID do último compromisso associado à sessão do usuário."""
        return self._last_event_ids.get(user_id)

    def add_history_message(self, user_id: int, role: str, text: str) -> None:
        """Adiciona uma mensagem ao histórico recente do usuário."""
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append({"role": role, "text": text})
        # Mantém as últimas 10 mensagens na janela
        if len(self._history[user_id]) > 10:
            self._history[user_id] = self._history[user_id][-10:]

    def get_recent_history_context(self, user_id: int, max_messages: int = 6) -> str:
        """Retorna o histórico formatado para alimentar o contexto do Gemini."""
        history = self._history.get(user_id, [])
        if not history:
            return ""
        recent = history[-max_messages:]
        lines = []
        for msg in recent:
            prefix = "Usuário" if msg["role"] == "user" else "Assistente"
            lines.append(f"{prefix}: {msg['text']}")
        return "\n".join(lines)

    def set_pending(self, user_id: int, proposal: PendingProposal) -> None:
        """Registra uma proposta pendente para o usuário."""
        self._pending_proposals[user_id] = proposal

    def get_pending(self, user_id: int) -> PendingProposal | None:
        """Obtém a proposta pendente do usuário, se houver."""
        return self._pending_proposals.get(user_id)

    def clear_pending(self, user_id: int) -> PendingProposal | None:
        """Remove e retorna a proposta pendente do usuário."""
        return self._pending_proposals.pop(user_id, None)

    def clear_session(self, user_id: int) -> None:
        """Limpa todo o estado em memória da sessão do usuário."""
        self.clear_pending(user_id)
        self._history.pop(user_id, None)
        self._last_event_ids.pop(user_id, None)

    @staticmethod
    def is_affirmative(text: str) -> bool:
        """Verifica se o texto contém palavras afirmativas em português brasileiro."""
        normalized = text.strip().lower()
        affirmative_words = {
            "sim", "s", "confirmo", "confirmado", "pode", "pode agendar",
            "pode criar", "pode sim", "beleza", "fechado", "fechou", "ok",
            "com certeza", "isso", "isso mesmo", "positivo", "vai fundo",
            "manda ver", "atualiza", "atualizar", "remarca", "remarcar",
        }
        if normalized in affirmative_words:
            return True
        return any(phrase in normalized for phrase in ["pode agendar", "pode sim", "isso mesmo", "com certeza"])

    @staticmethod
    def is_negative(text: str) -> bool:
        """Verifica se o texto contém palavras de negação ou recusa em português brasileiro."""
        normalized = text.strip().lower()
        negative_words = {
            "não", "nao", "n", "cancela", "cancelar", "deixa quieto",
            "deixa pra lá", "esquece", "não quero", "nao quero", "nem pensar",
            "deixa", "parar", "abortar",
        }
        if normalized in negative_words:
            return True
        return any(phrase in normalized for phrase in ["deixa quieto", "deixa pra lá", "não quero", "nao quero"])

    def evaluate_response(
        self,
        user_id: int,
        raw_text: str,
        ai_confirmation_flag: str | None = None,
    ) -> tuple[ResponseDisposition, PendingProposal | None]:
        """Avalia se a mensagem do usuário é uma confirmação, negação ou nova instrução."""
        pending = self.get_pending(user_id)
        if not pending:
            return ResponseDisposition.NO_PENDING, None

        # Prioriza classificação estruturada pela IA se fornecida
        if ai_confirmation_flag == "confirmed":
            self.clear_pending(user_id)
            return ResponseDisposition.CONFIRMED, pending

        if ai_confirmation_flag == "denied":
            self.clear_pending(user_id)
            return ResponseDisposition.DENIED, pending

        # Análise heurística das palavras em português
        if self.is_affirmative(raw_text):
            self.clear_pending(user_id)
            return ResponseDisposition.CONFIRMED, pending

        if self.is_negative(raw_text):
            self.clear_pending(user_id)
            return ResponseDisposition.DENIED, pending

        # Caso não seja claramente sim/não, mantém o estado e trata como nova entrada
        return ResponseDisposition.NEW_INPUT, pending
