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
)
from app.db.models import UserModel, EventModel
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
                return result_msg

            elif disposition == ResponseDisposition.DENIED:
                await self.idempotency_repo.update_status(msg_key, status="cancelled_by_user")
                return "Tudo bem, a proposta foi cancelada e nenhuma alteração foi feita na sua agenda."

        # 4. Interpretação Semântica por IA (Gemini)
        interpretation = await self.interpreter.interpret(
            raw_message=raw_text,
            anchor_datetime=anchor_dt,
            user_timezone=user.timezone,
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
            return "\n".join(validated.clarification_questions)

        # 6. Roteamento por Intenção
        if validated.intent == IntentType.CREATE_EVENT:
            return await self._handle_create_event(user, validated)

        elif validated.intent == IntentType.QUERY_EVENTS:
            return await self._handle_query_events(user, validated, anchor_dt)

        elif validated.intent == IntentType.CANCEL_EVENT:
            return await self._handle_cancel_proposal(user, validated, interpretation, anchor_dt)

        elif validated.intent == IntentType.UPDATE_EVENT:
            return await self._handle_update_proposal(user, validated, interpretation, anchor_dt)

        # Intenção desconhecida ou não mapeada
        return (
            "Não consegui identificar a ação desejada. Você pode me dizer para agendar um compromisso "
            "(ex: 'Dentista amanhã às 14h') ou consultar sua agenda (ex: 'O que tenho hoje?')."
        )

    async def _handle_create_event(self, user: UserModel, proposal: ValidatedProposal) -> str:
        """Processa intenção de criar evento com verificação de conflitos."""
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
            f"{participants_str}{inferred_note}"
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
        interpretation: AIInterpretationResult,
        anchor_dt: datetime,
    ) -> Optional[EventModel]:
        """Busca evento mais provável a partir de referências textuais."""
        ref = (interpretation.event_reference or interpretation.title or "").strip().lower()
        if not ref:
            return None

        # Busca eventos a partir de 30 dias atrás até 90 dias no futuro
        window_start = anchor_dt - timedelta(days=30)
        window_end = anchor_dt + timedelta(days=90)
        events = await self.event_repo.list_events_in_range(user_id, window_start, window_end)

        # 1. Correspondência exata ou de substring
        for ev in events:
            if ref in ev.title.lower() or ev.title.lower() in ref:
                return ev

        # 2. Correspondência aproximada por palavras-chave
        ref_words = set(ref.split())
        for ev in events:
            ev_words = set(ev.title.lower().split())
            if len(ref_words.intersection(ev_words)) >= 1:
                return ev

        return None

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
            d_str = self._format_date_br(event.start_time)
            h_start = self._format_time_br(event.start_time)
            h_end = self._format_time_br(event.end_time)
            return f"✅ Confirmado! Compromisso '{event.title}' agendado para {d_str}, das {h_start} às {h_end}."

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
