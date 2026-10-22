"""Testes da máquina de estados de propostas e confirmações conversacionais."""


from app.domain.enums import ActionType
from app.engine.state_machine import (
    ConversationStateManager,
    PendingProposal,
    ResponseDisposition,
)


def test_state_machine_pending_lifecycle():
    """Testa ciclo de vida de registro, consulta e limpeza de propostas pendentes."""
    manager = ConversationStateManager()
    user_id = 42

    assert manager.get_pending(user_id) is None

    proposal = PendingProposal(
        user_id=user_id,
        action_type=ActionType.UPDATE,
        target_event_id=10,
        summary="Remarcar 'Reunião Bernardo' de terça para quinta às 12h",
        data={"new_start": "2026-10-24T12:00:00"},
    )
    manager.set_pending(user_id, proposal)

    retrieved = manager.get_pending(user_id)
    assert retrieved is not None
    assert retrieved.action_type == ActionType.UPDATE
    assert retrieved.target_event_id == 10

    cleared = manager.clear_pending(user_id)
    assert cleared == proposal
    assert manager.get_pending(user_id) is None


def test_state_machine_affirmative_evaluations():
    """Testa reconhecimento de respostas afirmativas em português brasileiro."""
    manager = ConversationStateManager()
    user_id = 42

    proposal = PendingProposal(
        user_id=user_id,
        action_type=ActionType.CREATE,
        summary="Criar compromisso 'Consulta' quinta às 14h",
    )

    affirmative_inputs = [
        "sim",
        "SIM",
        "pode agendar",
        "pode sim",
        "fechado",
        "beleza",
        "confirmo",
        "com certeza",
        "isso mesmo",
    ]

    for user_input in affirmative_inputs:
        manager.set_pending(user_id, proposal)
        disposition, pending = manager.evaluate_response(user_id, user_input)
        assert disposition == ResponseDisposition.CONFIRMED
        assert pending is not None
        assert manager.get_pending(user_id) is None  # Estado foi limpo após confirmação


def test_state_machine_negative_evaluations():
    """Testa reconhecimento de respostas de negação/cancelamento."""
    manager = ConversationStateManager()
    user_id = 42

    proposal = PendingProposal(
        user_id=user_id,
        action_type=ActionType.CANCEL,
        summary="Cancelar reunião de amanhã",
    )

    negative_inputs = [
        "não",
        "nao",
        "cancela",
        "deixa quieto",
        "deixa pra lá",
        "esquece",
        "não quero",
    ]

    for user_input in negative_inputs:
        manager.set_pending(user_id, proposal)
        disposition, pending = manager.evaluate_response(user_id, user_input)
        assert disposition == ResponseDisposition.DENIED
        assert pending is not None
        assert manager.get_pending(user_id) is None  # Estado foi limpo após negação


def test_state_machine_new_input_preserves_state():
    """Testa que uma entrada não conclusiva mantém a proposta pendente."""
    manager = ConversationStateManager()
    user_id = 42

    proposal = PendingProposal(
        user_id=user_id,
        action_type=ActionType.UPDATE,
        summary="Remarcar reunião",
    )
    manager.set_pending(user_id, proposal)

    # Usuário responde algo que não é nem sim nem não diretamente
    disposition, pending = manager.evaluate_response(user_id, "mas e o almoço depois?")
    assert disposition == ResponseDisposition.NEW_INPUT
    assert pending is not None
    # Proposta continua preservada no estado
    assert manager.get_pending(user_id) is not None
