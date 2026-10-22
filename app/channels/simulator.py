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


import re


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

        # 1. Resposta afirmativa direta
        if text in {"sim", "confirmo", "pode agendar", "beleza", "fechado", "pode sim", "isso"}:
            return AIInterpretationResult(
                intent=IntentType.CREATE_EVENT,
                is_confirmation_or_denial="confirmed",
                confidence=ConfidenceLevel.HIGH,
            )

        # 2. Resposta negativa direta
        if text in {"não", "nao", "cancela", "deixa quieto", "esquece", "não quero", "nao quero"}:
            return AIInterpretationResult(
                intent=IntentType.UNKNOWN,
                is_confirmation_or_denial="denied",
                confidence=ConfidenceLevel.HIGH,
            )

        # 3. Lembretes avulsos / independentes (ex: "me lembra de tomar remédio daqui a 1h")
        standalone_rem_match = re.search(
            r"(?:lembr\w*\s+de|avisa\w*\s+para)\s+(.+?)(?:\s+daqui\s+a\s+(\d+)\s*(h|horas?|minutos?|min))?$",
            text,
        )
        if standalone_rem_match and not any(k in text for k in ["dia", "às", "as", "reunião", "consulta", "médico", "dentista"]):
            rem_title = standalone_rem_match.group(1).strip().capitalize()
            qty = int(standalone_rem_match.group(2)) if standalone_rem_match.group(2) else 1
            unit = standalone_rem_match.group(3) or "h"
            delta = timedelta(hours=qty) if "h" in unit else timedelta(minutes=qty)
            trigger_dt = anchor_datetime + delta
            return AIInterpretationResult(
                intent=IntentType.REMINDER_REQUEST,
                title=rem_title,
                reminder_message=rem_title,
                reminder_trigger_datetime=trigger_dt.isoformat(),
                confidence=ConfidenceLevel.HIGH,
            )

        # 4. Intenção pura de agendamento sem dados (ex: "AGENDAR COMPROMISSO", "quero agendar um compromisso")
        is_schedule_keyword = any(
            cmd in text for cmd in [
                "agendar compromisso", "marcar compromisso", "agendar reuniao", "agendar reunião",
                "quero agendar", "preciso agendar", "quero marcar", "preciso marcar"
            ]
        ) or text in ["agendar", "marcar", "novo compromisso", "novo evento"]

        has_any_time_or_date = bool(
            re.search(r"\d{1,2}|amanh[ãa]|hoje|segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo", text)
        )

        if is_schedule_keyword and not has_any_time_or_date:
            return AIInterpretationResult(
                intent=IntentType.CREATE_EVENT,
                title=None,
                start_datetime=None,
                missing_fields=["título/assunto", "data", "horário de início"],
                ambiguity_questions=["O que você gostaria de agendar e para qual data e horário?"],
                confidence=ConfidenceLevel.HIGH,
            )

        # 5. Consulta real de agenda (evita bater em 'agendar' pelo uso de palavras estritas)
        query_patterns = [
            "o que tenho", "meus compromissos", "ver agenda", "minha agenda",
            "consultar agenda", "quais compromissos", "o que tem", "tenho hoje",
            "tenho amanhã", "tenho amanha", "agenda de hoje", "agenda de amanhã"
        ]
        if any(qp in text for qp in query_patterns):
            return AIInterpretationResult(
                intent=IntentType.QUERY_EVENTS,
                start_datetime=anchor_datetime.isoformat(),
                confidence=ConfidenceLevel.HIGH,
            )

        # 6. Cancelamento
        if text.startswith("cancelar") or text.startswith("cancela"):
            parts = text.replace("cancelar", "").replace("cancela", "").strip()
            return AIInterpretationResult(
                intent=IntentType.CANCEL_EVENT,
                event_reference=parts or "compromisso",
                confidence=ConfidenceLevel.HIGH,
            )

        # 7. Criação de Evento com parsing rigoroso de data e hora brasileiros
        title = "Compromisso"
        if "dentista" in text:
            title = "Consulta com Dentista"
        elif "médico" in text or "medico" in text:
            title = "Consulta Médica"
        elif "reunião" in text or "reuniao" in text:
            title = "Reunião de Trabalho"
        elif "almoço" in text or "almoco" in text:
            title = "Almoço"

        # Parsing de Data
        target_date = None
        # Data DD/MM/AAAA
        m_date_full = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m_date_full:
            day, month, year = int(m_date_full.group(1)), int(m_date_full.group(2)), int(m_date_full.group(3))
            target_date = anchor_datetime.replace(year=year, month=month, day=day)
        else:
            # Data DD/MM
            m_date_short = re.search(r"(\d{1,2})/(\d{1,2})", text)
            if m_date_short:
                day, month = int(m_date_short.group(1)), int(m_date_short.group(2))
                target_date = anchor_datetime.replace(month=month, day=day)
            elif "amanhã" in text or "amanha" in text:
                target_date = anchor_datetime + timedelta(days=1)
            elif "hoje" in text:
                target_date = anchor_datetime
            else:
                m_dia = re.search(r"dia\s+(\d{1,2})", text)
                if m_dia:
                    target_date = anchor_datetime.replace(day=int(m_dia.group(1)))

        # Parsing de Horário
        hour = None
        minute = 0
        # Ex: "07:00 da manhã", "14:30", "07:00"
        m_time_colon = re.search(r"(\d{1,2}):(\d{2})(?:\s*(da manhã|da tarde|da noite))?", text)
        if m_time_colon:
            h = int(m_time_colon.group(1))
            minute = int(m_time_colon.group(2))
            period = m_time_colon.group(3)
            if period in {"da tarde", "da noite"} and h < 12:
                h += 12
            elif period == "da manhã" and h == 12:
                h = 0
            hour = h
        else:
            # Ex: "15h", "14h30", "7h"
            m_time_h = re.search(r"(\d{1,2})h(\d{2})?(?:\s*(da manhã|da tarde|da noite))?", text)
            if m_time_h:
                h = int(m_time_h.group(1))
                minute = int(m_time_h.group(2)) if m_time_h.group(2) else 0
                period = m_time_h.group(3)
                if period in {"da tarde", "da noite"} and h < 12:
                    h += 12
                hour = h
            else:
                # Ex: "às 7 da manhã", "às 14", "as 15"
                m_as = re.search(r"[àa]s\s+(\d{1,2})(?:\s*(da manhã|da tarde|da noite))?", text)
                if m_as:
                    h = int(m_as.group(1))
                    period = m_as.group(2)
                    if period in {"da tarde", "da noite"} and h < 12:
                        h += 12
                    hour = h

        # Parsing de Lembrete relativo (ex: "me lembra 2h antes", "avisa 15 minutos antes")
        reminder_minutes_before = None
        m_rem = re.search(r"(?:lembr\w*|avisa\w*)\s+(\d+)\s*(h|horas?|minutos?|min|dias?|dia)\s+antes", text)
        if m_rem:
            val = int(m_rem.group(1))
            unit = m_rem.group(2)
            if "dia" in unit:
                reminder_minutes_before = val * 1440
            elif "h" in unit:
                reminder_minutes_before = val * 60
            else:
                reminder_minutes_before = val

        # Se faltou horário ou data, preenche missing_fields
        missing_fields = []
        ambiguity_questions = []

        if target_date is None:
            missing_fields.append("data")
            ambiguity_questions.append(f"Para qual data você gostaria de agendar {title.lower()}?")
        if hour is None:
            missing_fields.append("horário de início")
            ambiguity_questions.append(f"Qual é o horário para {title.lower()}?")

        if missing_fields:
            start_iso = target_date.strftime("%Y-%m-%d") if target_date else None
            return AIInterpretationResult(
                intent=IntentType.CREATE_EVENT,
                title=title,
                start_datetime=start_iso,
                missing_fields=missing_fields,
                ambiguity_questions=ambiguity_questions,
                confidence=ConfidenceLevel.MEDIUM,
            )

        start_dt = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end_dt = start_dt + timedelta(hours=1)

        return AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title=title,
            start_datetime=start_dt.isoformat(),
            end_datetime=end_dt.isoformat(),
            reminder_minutes_before=reminder_minutes_before,
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
   /lembretes  -> Visualizar todos os lembretes configurados
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
    if mock_mode:
        interpreter = OfflineSimulatorMock()
        mode_label = "🟡 MOCK HEURÍSTICO OFFLINE (Forçado via argumento --mock)"
    else:
        # Tenta obrigatoriamente a inicialização do GeminiInterpreter
        gemini_key = settings.clean_gemini_api_key
        if not gemini_key:
            interpreter = OfflineSimulatorMock()
            mode_label = "🟡 MOCK HEURÍSTICO OFFLINE (GEMINI_API_KEY não encontrada no .env)"
            print("\n⚠️  [CONFIGURAÇÃO]: A variável 'GEMINI_API_KEY' não foi encontrada no arquivo .env.")
            print(f"    Arquivo .env verificado em: {settings.model_config.get('env_file', '.env')}")
            print("    Para utilizar o Gemini Real, configure GEMINI_API_KEY no arquivo .env.")
            print("    Iniciando em modo de contingência Mock Heurístico Offline.\n")
        else:
            try:
                real_interpreter = GeminiInterpreter(api_key=gemini_key)
                # Valida a inicialização da SDK oficial
                _ = real_interpreter.client
                interpreter = real_interpreter
                masked_key = f"{gemini_key[:6]}...{gemini_key[-4:]}" if len(gemini_key) > 10 else "***"
                mode_label = f"🟢 GEMINI REAL ({settings.GEMINI_MODEL}) [Chave ativa: {masked_key}]"
            except Exception as exc:
                interpreter = OfflineSimulatorMock()
                mode_label = f"🟡 MOCK HEURÍSTICO OFFLINE (Falha ao inicializar Gemini: {exc})"
                print(f"\n❌ [ERRO AO INICIALIZAR GEMINI REAL]: {exc}")
                print("    Alternando automaticamente para o Mock Heurístico Offline de contingência.\n")

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

        elif user_input.lower() == "/lembretes":
            async with async_session_factory() as session:
                service = AssistantService(session=session)
                user = await service.user_repo.get_or_create(phone, user_name)
                reminders = await service.reminder_repo.list_by_user(user.id)

                if not reminders:
                    print("\n🔔 Nenhum lembrete configurado no momento.")
                else:
                    print(f"\n🔔 Lembretes configurados ({len(reminders)}):")
                    for rem in reminders:
                        trigger_str = rem.send_at.strftime("%d/%m/%Y às %Hh%M")
                        status_badge = "⏳ [Pendente]" if rem.status == "pending" else f"[{rem.status}]"
                        ev_info = f" (Evento #{rem.event_id})" if rem.event_id else " (Lembrete Avulso)"
                        print(f"  • {status_badge} {trigger_str} - {rem.message_text}{ev_info}")
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
            await session.commit()
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
