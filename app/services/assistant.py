"""Serviço orquestrador do Meu Assistente (Pipeline de Conversa e Ações)."""

from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import EventStatus, ActionType, IntentType
from app.domain.exceptions import IdempotencyDuplicateError
from app.db.repository import (
    UserRepository,
    EventRepository,
    IdempotencyRepository,
    ChangeLogRepository,
    ReminderRepository,
)
from app.db.models import UserModel, EventModel, ReminderModel
from app.ai.client import AIInterpreterProtocol, GeminiInterpreter
from app.ai.schemas import AIInterpretationResult
from app.engine.validator import DeterministicValidator, ValidatedProposal
from app.engine.conflict import ConflictDetector, ConflictSeverity
from app.engine.state_machine import (
    ConversationStateManager,
    PendingProposal,
    ResponseDisposition,
)


class AssistantService:
    """Orquestrador central que integra Ingestão -> IA -> Validação -> Persistência -> Resposta."""

    def __init__(
        self,
        session: AsyncSession,
        interpreter: Optional[AIInterpreterProtocol] = None,
        state_manager: Optional[ConversationStateManager] = None,
    ):
        self.session = session
        self.interpreter = interpreter or GeminiInterpreter()
        self.state_manager = state_manager or ConversationStateManager()

        # Repositórios
        self.user_repo = UserRepository(session)
        self.event_repo = EventRepository(session)
        self.idempotency_repo = IdempotencyRepository(session)
        self.changelog_repo = ChangeLogRepository(session)
        self.reminder_repo = ReminderRepository(session)

    @staticmethod
    def _format_offset_br(minutes: int) -> str:
        """Formata intervalo de antecedência: ex '1h', '2h', '15 minutos', '1 dia'."""
        if minutes >= 1440 and minutes % 1440 == 0:
            days = minutes // 1440
            return f"{days} dia{'s' if days > 1 else ''}"
        if minutes >= 60 and minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours}h"
        return f"{minutes} minutos"

    @staticmethod
    def _format_date_br(dt: datetime) -> str:
        """Formata data no padrão brasileiro: ex 'Quinta-feira, 22/10/2026'."""
        dias_semana = {
            0: "Segunda-feira",
            1: "Terça-feira",
            2: "Quarta-feira",
            3: "Quinta-feira",
            4: "Sexta-feira",
            5: "Sábado",
            6: "Domingo",
        }
        dia_nome = dias_semana.get(dt.weekday(), "")
        return f"{dia_nome}, {dt.strftime('%d/%m/%Y')}"


    @staticmethod
    def _format_time_br(dt: datetime) -> str:
        """Formata hora: 14h ou 14h30."""
        if dt.minute == 0:
            return dt.strftime("%Hh")
        return dt.strftime("%Hh%M")

    async def process_message(
        self,
        phone_number: str,
        raw_text: str,
        idempotency_key: Optional[str] = None,
        user_name: str = "Usuário",
        anchor_datetime: Optional[datetime] = None,
    ) -> str:
        """Processa a mensagem do usuário de ponta a ponta e devolve a resposta final."""
        # 1. Obter ou registrar usuário
        user = await self.user_repo.get_or_create(
            phone_number=phone_number,
            display_name=user_name,
        )

        user_tz = ZoneInfo(user.timezone)
        if anchor_datetime is None:
            anchor_dt = datetime.now(user_tz)
        else:
            anchor_dt = (
                anchor_datetime.replace(tzinfo=user_tz)
                if anchor_datetime.tzinfo is None
                else anchor_datetime.astimezone(user_tz)
            )

        # 2. Controle de Idempotência
        msg_key = idempotency_key or f"{phone_number}_{int(anchor_dt.timestamp())}_{hash(raw_text)}"
        try:
            await self.idempotency_repo.register_message(
                idempotency_key=msg_key,
                user_id=user.id,
                raw_text=raw_text,
            )
        except IdempotencyDuplicateError:
            return "Esta mensagem já foi processada anteriormente."

        # 3. Verificar se há confirmação pendente na máquina de estados
        pending = self.state_manager.get_pending(user.id)
        if pending:
            disposition, evaluated_proposal = self.state_manager.evaluate_response(
                user_id=user.id,
                raw_text=raw_text,
            )

            if disposition == ResponseDisposition.CONFIRMED and evaluated_proposal:
                result_msg = await self._execute_pending_proposal(user, evaluated_proposal)
                await self.idempotency_repo.update_status(msg_key, status="processed")
                self.state_manager.add_history_message(user.id, role="user", text=raw_text)
                self.state_manager.add_history_message(user.id, role="assistant", text=result_msg)
                return result_msg

            elif disposition == ResponseDisposition.DENIED:
                await self.idempotency_repo.update_status(msg_key, status="cancelled_by_user")
                denied_msg = "Tudo bem, a proposta foi cancelada e nenhuma alteração foi feita na sua agenda."
                self.state_manager.add_history_message(user.id, role="user", text=raw_text)
                self.state_manager.add_history_message(user.id, role="assistant", text=denied_msg)
                return denied_msg

        # 4. Ingestão de Contexto Recente e Interpretação Semântica por IA (Gemini)
        recent_context = self.state_manager.get_recent_history_context(user.id, max_messages=6)
        interpretation = await self.interpreter.interpret(
            raw_message=raw_text,
            anchor_datetime=anchor_dt,
            user_timezone=user.timezone,
            recent_context=recent_context or None,
        )

        await self.idempotency_repo.update_status(
            msg_key,
            status="interpreted",
            detected_intent=interpretation.intent.value,
        )

        # 5. Validação Determinística
        validated = DeterministicValidator.validate_interpretation(
            interpretation=interpretation,
            user_timezone=user.timezone,
        )

        # Se houver dados ausentes ou necessidade de esclarecimento
        if validated.needs_clarification:
            response_text = "\n".join(validated.clarification_questions)

        # 6. Roteamento por Intenção
        elif validated.intent == IntentType.CREATE_EVENT:
            response_text = await self._handle_create_event(user, validated)

        elif validated.intent == IntentType.REMINDER_REQUEST:
            response_text = await self._handle_reminder_request(user, validated, interpretation, anchor_dt)

        elif validated.intent == IntentType.QUERY_EVENTS:
            response_text = await self._handle_query_events(user, validated, anchor_dt)

        elif validated.intent == IntentType.CANCEL_EVENT:
            response_text = await self._handle_cancel_proposal(user, validated, interpretation, anchor_dt)

        elif validated.intent == IntentType.UPDATE_EVENT:
            response_text = await self._handle_update_proposal(user, validated, interpretation, anchor_dt)

        else:
            response_text = (
                "Não consegui identificar a ação desejada. Você pode me dizer para agendar um compromisso "
                "(ex: 'Dentista amanhã às 14h') ou consultar sua agenda (ex: 'O que tenho hoje?')."
            )

        # Registra mensagens no histórico da conversa para resolução anafórica em turnos subsequentes
        self.state_manager.add_history_message(user.id, role="user", text=raw_text)
        self.state_manager.add_history_message(user.id, role="assistant", text=response_text)
        return response_text

    async def _handle_create_event(self, user: UserModel, proposal: ValidatedProposal) -> str:
        """Processa intenção de criar evento com verificação de conflitos e agendamento de lembrete."""
        assert proposal.start_time is not None
        assert proposal.end_time is not None

        # Busca sobreposição de horários com compromissos existentes
        conflicts = await self.event_repo.find_conflicts(
            owner_user_id=user.id,
            start_time=proposal.start_time,
            end_time=proposal.end_time,
        )
        conflict_report = ConflictDetector.analyze_conflicts(
            candidate_start=proposal.start_time,
            candidate_end=proposal.end_time,
            existing_events=conflicts,
        )

        # Se houver conflito, NÃO grava direto: solicita confirmação explícita
        if conflict_report.has_conflict:
            pending = PendingProposal(
                user_id=user.id,
                action_type=ActionType.CREATE,
                data={
                    "title": proposal.title,
                    "start_time": proposal.start_time.isoformat(),
                    "end_time": proposal.end_time.isoformat(),
                    "timezone": proposal.timezone_name,
                    "location_address": proposal.location_address,
                    "participants": proposal.participants,
                    "reminder_minutes_before": proposal.reminder_minutes_before,
                },
                summary=f"Criar '{proposal.title}' em {self._format_date_br(proposal.start_time)}",
            )
            self.state_manager.set_pending(user.id, pending)
            return conflict_report.warning_message or "Existe um conflito de horário. Deseja confirmar mesmo assim?"

        # Sem conflito: grava diretamente com auditoria
        event = await self.event_repo.create_event(
            owner_user_id=user.id,
            title=proposal.title or "Compromisso",
            start_time=proposal.start_time,
            end_time=proposal.end_time,
            timezone=proposal.timezone_name,
            location_address=proposal.location_address,
            participants_data=proposal.participants,
            status=EventStatus.CONFIRMED.value,
        )
        self.state_manager.set_last_event_id(user.id, event.id)

        # Lembrete Automático / Customizado
        reminder_minutes = (
            proposal.reminder_minutes_before
            if proposal.reminder_minutes_before is not None
            else getattr(user, "default_reminder_minutes", 60)
        )
        reminder_str = ""
        if reminder_minutes > 0:
            reminder_send_at = event.start_time - timedelta(minutes=reminder_minutes)
            reminder = await self.reminder_repo.create_reminder(
                owner_user_id=user.id,
                event_id=event.id,
                recipient_phone=user.phone_number,
                message_text=f"Lembrete: {event.title} às {self._format_time_br(event.start_time)}",
                send_at=reminder_send_at,
            )
            rem_d = self._format_date_br(reminder.send_at)
            rem_h = self._format_time_br(reminder.send_at)
            offset_desc = self._format_offset_br(reminder_minutes)
            reminder_str = f"\n🔔 Lembrete configurado para {rem_d} às {rem_h} ({offset_desc} antes)."

        date_str = self._format_date_br(event.start_time)
        start_str = self._format_time_br(event.start_time)
        end_str = self._format_time_br(event.end_time)
        loc_str = f" no local: {event.location.raw_address}" if event.location else ""

        participants_str = ""
        if event.participants:
            names = [p.name for p in event.participants]
            participants_str = f"\n👥 Participantes: {', '.join(names)}"

        inferred_note = "\nℹ️ (Duração estimada em 1h; você pode ajustar se desejar)" if proposal.duration_inferred else ""

        return (
            f"✅ Compromisso agendado com sucesso!\n"
            f"📌 {event.title}\n"
            f"📅 {date_str}, das {start_str} às {end_str}{loc_str}"
            f"{participants_str}{reminder_str}{inferred_note}"
        )

    async def _handle_reminder_request(
        self,
        user: UserModel,
        proposal: ValidatedProposal,
        interpretation: AIInterpretationResult,
        anchor_dt: datetime,
    ) -> str:
        """Processa pedidos de lembrete (avulso ou vinculado a compromisso existente via anáfora)."""
        ref_text = (
            proposal.event_reference
            or interpretation.event_reference
            or proposal.title
            or ""
        )

        # 1. Tenta vincular o lembrete a um evento existente (se houver referência anafórica ou tempo relativo)
        target_event = None
        is_relative_to_event = bool(
            proposal.reminder_minutes_before is not None
            or interpretation.reminder_minutes_before is not None
            or any(w in ref_text.lower() for w in ["dessa", "desse", "dele", "dela", "consulta", "reunião", "reuniao", "compromisso", "evento"])
            or self.state_manager.get_last_event_id(user.id) is not None
        )

        if is_relative_to_event:
            target_event = await self._find_matching_event(user.id, ref_text, anchor_dt)

        if target_event:
            minutes_before = (
                proposal.reminder_minutes_before
                or interpretation.reminder_minutes_before
                or getattr(user, "default_reminder_minutes", 60)
            )
            trigger_dt = target_event.start_time - timedelta(minutes=minutes_before)
            offset_text = self._format_offset_br(minutes_before)
            msg = f"Lembrete: {target_event.title} às {self._format_time_br(target_event.start_time)}"

            reminder = await self.reminder_repo.create_reminder(
                owner_user_id=user.id,
                event_id=target_event.id,
                recipient_phone=user.phone_number,
                message_text=msg,
                send_at=trigger_dt,
            )

            d_str = self._format_date_br(reminder.send_at)
            h_str = self._format_time_br(reminder.send_at)
            return (
                f"🔔 Lembrete configurado com sucesso para o compromisso '{target_event.title}'!\n"
                f"📅 Disparo programado para {d_str} às {h_str} ({offset_text} antes do evento)."
            )

        # 2. Lembrete avulso sem vínculo a evento
        trigger_dt = proposal.reminder_trigger_datetime or (anchor_dt + timedelta(hours=1))
        msg = proposal.reminder_message or proposal.title or "Lembrete"

        reminder = await self.reminder_repo.create_reminder(
            owner_user_id=user.id,
            recipient_phone=user.phone_number,
            message_text=msg,
            send_at=trigger_dt,
        )

        d_str = self._format_date_br(reminder.send_at)
        h_str = self._format_time_br(reminder.send_at)

        return (
            f"🔔 Lembrete agendado com sucesso!\n"
            f"📌 {reminder.message_text}\n"
            f"⏰ Disparo programado para {d_str} às {h_str}."
        )

    async def _handle_query_events(
        self,
        user: UserModel,
        proposal: ValidatedProposal,
        anchor_dt: datetime,
    ) -> str:
        """Processa consultas de agenda."""
        start_search = proposal.start_time or anchor_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_search = proposal.end_time or (start_search + timedelta(days=2))

        events = await self.event_repo.list_events_in_range(
            owner_user_id=user.id,
            start_time=start_search,
            end_time=end_search,
        )

        if not events:
            return "📅 Você não tem nenhum compromisso marcado para este período."

        lines = [f"📅 Seus compromissos encontrados ({len(events)}):"]
        for ev in events:
            d_str = self._format_date_br(ev.start_time)
            h_start = self._format_time_br(ev.start_time)
            h_end = self._format_time_br(ev.end_time)
            loc = f" 📍 {ev.location.raw_address}" if ev.location else ""
            lines.append(f"• {d_str} das {h_start} às {h_end}: {ev.title}{loc}")

        return "\n".join(lines)

    async def _handle_cancel_proposal(
        self,
        user: UserModel,
        proposal: ValidatedProposal,
        interpretation: AIInterpretationResult,
        anchor_dt: datetime,
    ) -> str:
        """Encontra o evento a cancelar e solicita confirmação explícita."""
        target_event = await self._find_matching_event(user.id, interpretation, anchor_dt)
        if not target_event:
            return "Não encontrei nenhum compromisso correspondente para cancelar. Poderia especificar o nome ou a data?"

        pending = PendingProposal(
            user_id=user.id,
            action_type=ActionType.CANCEL,
            target_event_id=target_event.id,
            data={},
            summary=f"Cancelar '{target_event.title}'",
        )
        self.state_manager.set_pending(user.id, pending)

        d_str = self._format_date_br(target_event.start_time)
        h_start = self._format_time_br(target_event.start_time)
        return f"Encontrei '{target_event.title}' em {d_str} às {h_start}. Tem certeza que deseja cancelar esse compromisso?"

    async def _handle_update_proposal(
        self,
        user: UserModel,
        proposal: ValidatedProposal,
        interpretation: AIInterpretationResult,
        anchor_dt: datetime,
    ) -> str:
        """Encontra evento a atualizar e propõe mudança para confirmação."""
        target_event = await self._find_matching_event(user.id, interpretation, anchor_dt)
        if not target_event:
            return "Não encontrei o compromisso mencionado para alterar. Poderia especificar o nome atual dele?"

        updates = {}
        if proposal.start_time:
            updates["start_time"] = proposal.start_time
        if proposal.end_time:
            updates["end_time"] = proposal.end_time
        if proposal.title and proposal.title != target_event.title:
            updates["title"] = proposal.title
        if proposal.location_address:
            updates["location_address"] = proposal.location_address

        pending = PendingProposal(
            user_id=user.id,
            action_type=ActionType.UPDATE,
            target_event_id=target_event.id,
            data={k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in updates.items()},
            summary=f"Atualizar '{target_event.title}'",
        )
        self.state_manager.set_pending(user.id, pending)

        summary_parts = []
        if proposal.start_time:
            summary_parts.append(f"data para {self._format_date_br(proposal.start_time)} às {self._format_time_br(proposal.start_time)}")

        change_desc = ", ".join(summary_parts) if summary_parts else "os dados indicados"
        return f"Encontrei '{target_event.title}'. A mensagem indica mudança de {change_desc}. Quer atualizar esse compromisso?"

    async def _find_matching_event(
        self,
        user_id: int,
        reference: str | AIInterpretationResult,
        anchor_dt: datetime,
    ) -> Optional[EventModel]:
        """Busca evento mais provável a partir de referências textuais ou do histórico de sessão."""
        if isinstance(reference, AIInterpretationResult):
            ref = (reference.event_reference or reference.title or "").strip().lower()
        else:
            ref = (reference or "").strip().lower()

        last_event_id = self.state_manager.get_last_event_id(user_id)
        generic_markers = {
            "dessa", "desse", "dele", "dela", "deste", "desta",
            "consulta", "compromisso", "reunião", "reuniao", "evento",
            "dessa consulta", "desse compromisso", "dessa reuniao", "desse evento",
            "essa consulta", "esse compromisso", "esse evento", "da consulta", "do evento"
        }

        # 1. Se houver last_event_id e a referência for genérica/anafórica
        if last_event_id and (not ref or ref in generic_markers or any(m in ref for m in ["dessa", "desse", "dele", "dela"])):
            ev = await self.event_repo.get_by_id(last_event_id, owner_user_id=user_id)
            if ev:
                return ev

        # 2. Busca eventos na janela temporal (-30 a +90 dias)
        window_start = anchor_dt - timedelta(days=30)
        window_end = anchor_dt + timedelta(days=90)
        events = await self.event_repo.list_events_in_range(user_id, window_start, window_end)

        # 3. Correspondência exata ou de substring
        if ref and ref not in generic_markers:
            for ev in events:
                if ref in ev.title.lower() or ev.title.lower() in ref:
                    return ev

            # 4. Correspondência aproximada por palavras-chave
            stop_words = {"de", "para", "o", "a", "do", "da", "um", "uma", "com", "em", "no", "na", "esse", "essa", "desse", "dessa", "deste", "desta"}
            ref_words = set(w for w in ref.split() if w not in stop_words)
            if ref_words:
                for ev in events:
                    ev_words = set(ev.title.lower().split())
                    if ref_words.intersection(ev_words):
                        return ev

        # 5. Se não encontrou por texto mas há um último evento ativo no estado
        if last_event_id:
            ev = await self.event_repo.get_by_id(last_event_id, owner_user_id=user_id)
            if ev:
                return ev

        # 6. Fallback para o evento futuro mais próximo do usuário
        upcoming = [ev for ev in events if ev.start_time >= anchor_dt]
        if upcoming:
            return upcoming[0]

        return events[0] if events else None

    async def _execute_pending_proposal(
        self,
        user: UserModel,
        proposal: PendingProposal,
    ) -> str:
        """Executa de forma transacional a ação confirmada pelo usuário."""
        if proposal.action_type == ActionType.CREATE:
            start_time = datetime.fromisoformat(proposal.data["start_time"])
            end_time = datetime.fromisoformat(proposal.data["end_time"])

            event = await self.event_repo.create_event(
                owner_user_id=user.id,
                title=proposal.data["title"],
                start_time=start_time,
                end_time=end_time,
                timezone=proposal.data.get("timezone", user.timezone),
                location_address=proposal.data.get("location_address"),
                participants_data=proposal.data.get("participants"),
                status=EventStatus.CONFIRMED.value,
            )
            self.state_manager.set_last_event_id(user.id, event.id)
            reminder_minutes = (
                proposal.data.get("reminder_minutes_before")
                if proposal.data.get("reminder_minutes_before") is not None
                else getattr(user, "default_reminder_minutes", 60)
            )
            reminder_str = ""
            if reminder_minutes > 0:
                reminder_send_at = event.start_time - timedelta(minutes=reminder_minutes)
                reminder = await self.reminder_repo.create_reminder(
                    owner_user_id=user.id,
                    event_id=event.id,
                    recipient_phone=user.phone_number,
                    message_text=f"Lembrete: {event.title} às {self._format_time_br(event.start_time)}",
                    send_at=reminder_send_at,
                )
                rem_d = self._format_date_br(reminder.send_at)
                rem_h = self._format_time_br(reminder.send_at)
                offset_desc = self._format_offset_br(reminder_minutes)
                reminder_str = f"\n🔔 Lembrete configurado para {rem_d} às {rem_h} ({offset_desc} antes)."

            d_str = self._format_date_br(event.start_time)
            h_start = self._format_time_br(event.start_time)
            h_end = self._format_time_br(event.end_time)
            return f"✅ Confirmado! Compromisso '{event.title}' agendado para {d_str}, das {h_start} às {h_end}.{reminder_str}"


        elif proposal.action_type == ActionType.UPDATE:
            assert proposal.target_event_id is not None
            raw_updates = proposal.data
            parsed_updates = {}
            for k, v in raw_updates.items():
                if "time" in k and isinstance(v, str):
                    parsed_updates[k] = datetime.fromisoformat(v)
                else:
                    parsed_updates[k] = v

            updated = await self.event_repo.update_event(
                event_id=proposal.target_event_id,
                owner_user_id=user.id,
                updates=parsed_updates,
                reason="Atualização confirmada pelo usuário via mensagem",
            )
            return f"✅ Pronto! O compromisso '{updated.title}' foi atualizado com sucesso."

        elif proposal.action_type == ActionType.CANCEL:
            assert proposal.target_event_id is not None
            cancelled = await self.event_repo.cancel_event(
                event_id=proposal.target_event_id,
                owner_user_id=user.id,
                reason="Cancelamento confirmado pelo usuário via mensagem",
            )
            return f"🗑️ O compromisso '{cancelled.title}' foi cancelado."

        return "Ação executada com sucesso."
