"""Simulador de Terminal Interativo (CLI) para o Meu Assistente via WhatsApp."""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select

# Garante suporte a UTF-8 no terminal Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from app.config import settings
from app.db.session import init_db, async_session_factory
from app.db.models import EventModel, ChangeLogModel
from app.services.assistant import AssistantService
from app.engine.state_machine import ConversationStateManager
from app.ai.client import GeminiInterpreter, AIInterpreterProtocol
from app.ai.schemas import AIInterpretationResult
from app.domain.enums import IntentType, ConfidenceLevel


class OfflineSimulatorMock(AIInterpreterProtocol):
    """Interpretador heurístico offline para testar o CLI mesmo sem conexão com a internet."""

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: str | None = None,
    ) -> AIInterpretationResult:
        text = raw_message.lower().strip()

        # Resposta afirmativa direta
        if text in {"sim", "confirmo", "pode agendar", "beleza", "fechado"}:
            return AIInterpretationResult(
                intent=IntentType.CREATE_EVENT,
                is_confirmation_or_denial="confirmed",
                confidence=ConfidenceLevel.HIGH,
            )

        # Resposta negativa direta
        if text in {"não", "nao", "cancela", "deixa quieto", "esquece"}:
            return AIInterpretationResult(
                intent=IntentType.UNKNOWN,
                is_confirmation_or_denial="denied",
                confidence=ConfidenceLevel.HIGH,
            )

        # Consulta
        if any(w in text for w in ["o que tenho", "compromissos", "agenda", "o que tem", "tenho hoje", "tenho amanhã"]):
            return AIInterpretationResult(
                intent=IntentType.QUERY_EVENTS,
                start_datetime=anchor_datetime.isoformat(),
                confidence=ConfidenceLevel.HIGH,
            )

        # Cancelamento
        if "cancelar" in text or "cancela" in text:
            parts = text.replace("cancelar", "").replace("cancela", "").strip()
            return AIInterpretationResult(
                intent=IntentType.CANCEL_EVENT,
                event_reference=parts or "compromisso",
                confidence=ConfidenceLevel.HIGH,
            )

        # Criação simples heurística
        title = "Compromisso"
        if "dentista" in text:
            title = "Consulta com Dentista"
        elif "reunião" in text or "reuniao" in text:
            title = "Reunião de Trabalho"
        elif "médico" in text or "medico" in text:
            title = "Consulta Médica"
        elif "almoço" in text or "almoco" in text:
            title = "Almoço"

        # Detecta amanhã ou hoje
        target_date = anchor_datetime
        if "amanhã" in text or "amanha" in text:
            target_date = anchor_datetime + timedelta(days=1)

        # Detecta horário simples
        hour = 14
        if "15h" in text or "às 15" in text:
            hour = 15
        elif "10h" in text or "às 10" in text:
            hour = 10
        elif "12h" in text or "ao meio-dia" in text or "meio dia" in text:
            hour = 12
        elif "16h" in text or "às 16" in text:
            hour = 16

        start_dt = target_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(hours=1)

        return AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title=title,
            start_datetime=start_dt.isoformat(),
            end_datetime=end_dt.isoformat(),
            extracted_facts=[raw_message],
            confidence=ConfidenceLevel.HIGH,
        )


BANNER = """
================================================================================
           🤖  MEU ASSISTENTE - SIMULADOR WHATSAPP (TERMINAL CLI)  🤖
================================================================================
 Interface para testar toda a inteligência conversacional, conflitos e auditoria.
 Fuso Horário Atual: {timezone} | Hora Âncora: {now}
 Modo Ativo: {mode}
--------------------------------------------------------------------------------
 COMANDOS RÁPIDOS:
   /agenda     -> Visualizar todos os compromissos salvos no banco
   /auditoria  -> Ver o histórico append-only de alterações (change_log)
   /limpar     -> Resetar propostas pendentes e limpar memória da conversa
   /ajuda      -> Exibir esta lista de comandos
   /sair       -> Encerrar o simulador
================================================================================
"""


async def run_simulator(mock_mode: bool = False, phone: str = "5511999998888", user_name: str = "Você") -> None:
    """Executa o loop interativo de conversação no terminal."""
    # Garante que o banco de dados e as tabelas estejam criados
    await init_db()

    user_tz = ZoneInfo(settings.DEFAULT_TIMEZONE)
    now_str = datetime.now(user_tz).strftime("%d/%m/%Y %H:%M")

    # Seleciona o interpretador (Gemini Real ou Mock)
    if mock_mode or not settings.GEMINI_API_KEY:
        interpreter = OfflineSimulatorMock()
        mode_label = "🟡 MOCK HEURÍSTICO OFFLINE (Sem cota de API)"
    else:
        interpreter = GeminiInterpreter()
        mode_label = f"🟢 GEMINI REAL ({settings.GEMINI_MODEL})"

    state_manager = ConversationStateManager()

    print(BANNER.format(timezone=settings.DEFAULT_TIMEZONE, now=now_str, mode=mode_label))

    while True:
        try:
            print("\n" + "-" * 80)
            user_input = input(f"💬 {user_name}: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nSaindo do simulador. Até logo!")
            break

        if not user_input:
            continue

        # Tratamento de comandos especiais
        if user_input.lower() in {"/sair", "/exit", "sair"}:
            print("\n👋 Sessão encerrada. Até a próxima!")
            break

        elif user_input.lower() in {"/ajuda", "/help"}:
            print(BANNER.format(timezone=settings.DEFAULT_TIMEZONE, now=now_str, mode=mode_label))
            continue

        elif user_input.lower() == "/limpar":
            async with async_session_factory() as session:
                service = AssistantService(session=session, state_manager=state_manager)
                user = await service.user_repo.get_or_create(phone, user_name)
                state_manager.clear_pending(user.id)
            print("\n🧹 Memória e propostas pendentes da conversa foram resetadas com sucesso.")
            continue

        elif user_input.lower() == "/agenda":
            async with async_session_factory() as session:
                service = AssistantService(session=session)
                user = await service.user_repo.get_or_create(phone, user_name)
                start_window = datetime.now(user_tz) - timedelta(days=7)
                end_window = datetime.now(user_tz) + timedelta(days=60)
                events = await service.event_repo.list_events_in_range(
                    owner_user_id=user.id,
                    start_time=start_window,
                    end_time=end_window,
                    include_cancelled=True,
                )

                if not events:
                    print("\n📭 Nenhum compromisso encontrado no banco de dados.")
                else:
                    print(f"\n📋 Compromissos cadastrados no banco ({len(events)}):")
                    for ev in events:
                        d_str = ev.start_time.strftime("%d/%m/%Y")
                        h_start = ev.start_time.strftime("%Hh%M")
                        h_end = ev.end_time.strftime("%Hh%M")
                        status_emoji = "✅" if ev.status == "confirmed" else ("⏳" if ev.status == "draft" else "❌")
                        loc = f" | Local: {ev.location.raw_address}" if ev.location else ""
                        print(f"  {status_emoji} [{ev.id}] {d_str} {h_start}-{h_end} - {ev.title} (Status: {ev.status}){loc}")
            continue

        elif user_input.lower() == "/auditoria":
            async with async_session_factory() as session:
                service = AssistantService(session=session)
                user = await service.user_repo.get_or_create(phone, user_name)
                query = (
                    select(ChangeLogModel)
                    .where(ChangeLogModel.author_user_id == user.id)
                    .order_by(ChangeLogModel.created_at.desc())
                    .limit(10)
                )
                result = await session.execute(query)
                logs = result.scalars().all()

                if not logs:
                    print("\n📜 Nenhuma entrada na trilha de auditoria ainda.")
                else:
                    print(f"\n📜 Últimos registros da trilha de auditoria (change_log):")
                    for log in logs:
                        dt_str = log.created_at.strftime("%d/%m %H:%M:%S")
                        print(f"  • [{dt_str}] Ação: {log.action.upper()} em {log.entity_name} #{log.entity_id} | Motivo: {log.reason}")
                        if log.previous_values:
                            print(f"    Valores Anteriores: {log.previous_values}")
                        if log.new_values:
                            print(f"    Novos Valores: {log.new_values}")
            continue

        # Processamento regular da mensagem conversacional
        print("\n⏳ Assistente digitando...\n")
        async with async_session_factory() as session:
            service = AssistantService(
                session=session,
                interpreter=interpreter,
                state_manager=state_manager,
            )
            response = await service.process_message(
                phone_number=phone,
                raw_text=user_input,
                user_name=user_name,
            )
            print(f"🤖 Assistente:\n{response}")


def main():
    """Ponto de entrada do script CLI."""
    parser = argparse.ArgumentParser(description="Simulador CLI do Meu Assistente")
    parser.add_argument("--mock", action="store_true", help="Executa o simulador no modo mock offline")
    parser.add_argument("--phone", default="5511999998888", help="Número de telefone fictício do usuário")
    parser.add_argument("--name", default="Você", help="Nome de exibição do usuário")

    args = parser.parse_args()

    # Executa a aplicação assíncrona
    asyncio.run(run_simulator(mock_mode=args.mock, phone=args.phone, user_name=args.name))


if __name__ == "__main__":
    main()
