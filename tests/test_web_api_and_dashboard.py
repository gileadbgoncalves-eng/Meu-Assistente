"""Testes para a API REST v1 (/api/v1/*) e Dashboard Web (/painel)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


@pytest.mark.asyncio
async def test_api_events_crud_and_conflict_409(api_client: AsyncClient):
    """Valida ciclo completo de eventos: criação, conflito 409, listagem, patch e delete."""
    headers = {"X-User-Phone": "5511999990001"}

    # 1. Cria evento inicial (10h às 11h)
    start_dt = datetime(2026, 11, 10, 10, 0, tzinfo=SAO_PAULO_TZ)
    end_dt = datetime(2026, 11, 10, 11, 0, tzinfo=SAO_PAULO_TZ)

    payload = {
        "title": "Reunião de Planejamento",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "location": "Av. Paulista, 1000",
        "travel_time_minutes": 20,
        "reminder_minutes_before": 30,
    }

    res_create = await api_client.post("/api/v1/events", json=payload, headers=headers)
    assert res_create.status_code == 201
    event_data = res_create.json()
    assert event_data["title"] == "Reunião de Planejamento"
    assert event_data["location"] == "Av. Paulista, 1000"
    event_id = event_data["id"]

    # 2. Tenta criar evento conflitante no mesmo horário -> Deve retornar HTTP 409 Conflict
    conflict_payload = {
        "title": "Consulta Médica Conflitante",
        "start_time": datetime(2026, 11, 10, 10, 30, tzinfo=SAO_PAULO_TZ).isoformat(),
        "end_time": datetime(2026, 11, 10, 11, 30, tzinfo=SAO_PAULO_TZ).isoformat(),
    }
    res_conflict = await api_client.post("/api/v1/events", json=conflict_payload, headers=headers)
    assert res_conflict.status_code == 409
    assert "conflito" in res_conflict.json()["detail"].lower()

    # 3. Listagem de eventos
    res_list = await api_client.get("/api/v1/events", headers=headers)
    assert res_list.status_code == 200
    events_list = res_list.json()
    assert len(events_list) >= 1
    assert any(ev["id"] == event_id for ev in events_list)

    # 4. Atualização parcial (PATCH)
    res_patch = await api_client.patch(
        f"/api/v1/events/{event_id}",
        json={"title": "Reunião de Planejamento Estratégico"},
        headers=headers,
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["title"] == "Reunião de Planejamento Estratégico"

    # 5. Cancelamento/Remoção (DELETE)
    res_delete = await api_client.delete(f"/api/v1/events/{event_id}", headers=headers)
    assert res_delete.status_code == 200
    assert res_delete.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_contacts_crud(api_client: AsyncClient):
    """Valida CRUD completo de contatos via API REST."""
    headers = {"X-User-Phone": "5511999990001"}

    # 1. Criação de contato
    contact_data = {
        "name": "Dr. Fernando Silveira",
        "phone": "5511988887777",
        "email": "fernando@clinica.com",
        "category": "Médico",
        "notes": "Cardiologista de confiança",
    }
    res_create = await api_client.post("/api/v1/contacts", json=contact_data, headers=headers)
    assert res_create.status_code == 201
    contact = res_create.json()
    assert contact["name"] == "Dr. Fernando Silveira"
    contact_id = contact["id"]

    # 2. Listagem geral
    res_list = await api_client.get("/api/v1/contacts", headers=headers)
    assert res_list.status_code == 200
    assert any(c["id"] == contact_id for c in res_list.json())

    # 3. Busca por nome
    res_search = await api_client.get("/api/v1/contacts?q=Fernando", headers=headers)
    assert res_search.status_code == 200
    assert len(res_search.json()) >= 1
    assert res_search.json()[0]["name"] == "Dr. Fernando Silveira"

    # 4. Atualização de contato
    res_update = await api_client.put(
        f"/api/v1/contacts/{contact_id}",
        json={"category": "Cardiologista"},
        headers=headers,
    )
    assert res_update.status_code == 200
    assert res_update.json()["category"] == "Cardiologista"

    # 5. Deleção de contato
    res_delete = await api_client.delete(f"/api/v1/contacts/{contact_id}", headers=headers)
    assert res_delete.status_code == 200
    assert res_delete.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_api_briefing_reminders_and_status(api_client: AsyncClient):
    """Valida endpoints de briefing, lembretes e status do sistema."""
    headers = {"X-User-Phone": "5511999990001"}

    # 1. Briefing
    res_briefing = await api_client.get("/api/v1/briefing", headers=headers)
    assert res_briefing.status_code == 200
    briefing_json = res_briefing.json()
    assert "briefing_text" in briefing_json
    assert "target_date" in briefing_json

    # 2. Lembretes
    res_reminders = await api_client.get("/api/v1/reminders", headers=headers)
    assert res_reminders.status_code == 200
    assert isinstance(res_reminders.json(), list)

    # 3. Status
    res_status = await api_client.get("/api/v1/status", headers=headers)
    assert res_status.status_code == 200
    status_json = res_status.json()
    assert status_json["status"] == "operational"
    assert status_json["version"] == "0.3.0-RC"


@pytest.mark.asyncio
async def test_dashboard_page_get(api_client: AsyncClient):
    """Valida se a página do painel embutido HTML é servida corretamente em /painel e /dashboard."""
    for path in ["/painel", "/dashboard"]:
        res = await api_client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Meu Assistente" in res.text
        assert "Painel Executivo" in res.text
        assert "Compromissos Agendados" in res.text


@pytest.mark.asyncio
async def test_api_events_time_validation_end_before_start(api_client: AsyncClient):
    """Garante que tentativa de criar evento com término anterior ao início retorna HTTP 400."""
    headers = {"X-User-Phone": "5511999990001"}
    start_dt = datetime(2026, 11, 10, 14, 0, tzinfo=SAO_PAULO_TZ)
    end_dt = datetime(2026, 11, 10, 13, 0, tzinfo=SAO_PAULO_TZ)

    payload = {
        "title": "Evento Invalido",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
    }
    res = await api_client.post("/api/v1/events", json=payload, headers=headers)
    assert res.status_code == 400
    assert "término" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_events_patch_not_found(api_client: AsyncClient):
    """Garante que atualizar evento inexistente retorna HTTP 404."""
    headers = {"X-User-Phone": "5511999990001"}
    res = await api_client.patch(
        "/api/v1/events/999999",
        json={"title": "Novo Titulo"},
        headers=headers,
    )
    assert res.status_code == 404 or res.status_code == 400


@pytest.mark.asyncio
async def test_api_contacts_update_and_delete_not_found(api_client: AsyncClient):
    """Garante que atualizar ou remover contato inexistente retorna HTTP 404."""
    headers = {"X-User-Phone": "5511999990001"}
    res_update = await api_client.put(
        "/api/v1/contacts/999999",
        json={"name": "Inexistente"},
        headers=headers,
    )
    assert res_update.status_code == 404

    res_delete = await api_client.delete(
        "/api/v1/contacts/999999",
        headers=headers,
    )
    assert res_delete.status_code == 404


@pytest.mark.asyncio
async def test_api_events_filter_by_date_range(api_client: AsyncClient):
    """Valida filtragem de compromissos por start_date e end_date."""
    headers = {"X-User-Phone": "5511999990001"}

    # Evento em Dezembro 2026
    start_dec = datetime(2026, 12, 1, 10, 0, tzinfo=SAO_PAULO_TZ)
    end_dec = datetime(2026, 12, 1, 11, 0, tzinfo=SAO_PAULO_TZ)
    await api_client.post(
        "/api/v1/events",
        json={"title": "Reunião de Dezembro", "start_time": start_dec.isoformat(), "end_time": end_dec.isoformat()},
        headers=headers,
    )

    # Consulta apenas Novembro 2026 (não deve conter o evento de Dezembro)
    res = await api_client.get(
        "/api/v1/events",
        params={
            "start_date": datetime(2026, 11, 1, 0, 0, tzinfo=SAO_PAULO_TZ).isoformat(),
            "end_date": datetime(2026, 11, 30, 23, 59, tzinfo=SAO_PAULO_TZ).isoformat(),
        },
        headers=headers,
    )
    assert res.status_code == 200
    events = res.json()
    assert not any(ev["title"] == "Reunião de Dezembro" for ev in events)


@pytest.mark.asyncio
async def test_api_contacts_multiuser_isolation(api_client: AsyncClient):
    """Garante isolamento estrito de contatos entre usuários distintos."""
    headers_user_a = {"X-User-Phone": "5511999990001"}
    headers_user_b = {"X-User-Phone": "5511999990002"}

    # Usuário A cria um contato secreto
    res = await api_client.post(
        "/api/v1/contacts",
        json={"name": "Contato Confidencial de A", "phone": "5511988880000"},
        headers=headers_user_a,
    )
    assert res.status_code == 201

    # Usuário B lista contatos -> NÃO deve ver o contato de A
    res_b = await api_client.get("/api/v1/contacts", headers=headers_user_b)
    assert res_b.status_code == 200
    contacts_b = res_b.json()
    assert not any(c["name"] == "Contato Confidencial de A" for c in contacts_b)


@pytest.mark.asyncio
async def test_api_events_multiuser_isolation(api_client: AsyncClient):
    """Garante isolamento estrito de eventos entre usuários distintos."""
    headers_user_a = {"X-User-Phone": "5511999990001"}
    headers_user_b = {"X-User-Phone": "5511999990002"}

    # Usuário A cria um evento
    start_dt = datetime(2026, 11, 15, 14, 0, tzinfo=SAO_PAULO_TZ)
    end_dt = datetime(2026, 11, 15, 15, 0, tzinfo=SAO_PAULO_TZ)
    res = await api_client.post(
        "/api/v1/events",
        json={"title": "Evento Privado de A", "start_time": start_dt.isoformat(), "end_time": end_dt.isoformat()},
        headers=headers_user_a,
    )
    assert res.status_code == 201
    event_id = res.json()["id"]

    # Usuário B tenta deletar o evento de A -> Não deve conseguir cancelar o de A
    res_del = await api_client.delete(f"/api/v1/events/{event_id}", headers=headers_user_b)
    assert res_del.status_code in {200, 404, 403}

    # Verifica se o evento continua ativo na lista do Usuário A
    res_a = await api_client.get("/api/v1/events", headers=headers_user_a)
    assert res_a.status_code == 200
    assert any(ev["id"] == event_id and ev["status"] == "confirmed" for ev in res_a.json())


@pytest.mark.asyncio
async def test_api_events_with_participants_and_edit(api_client: AsyncClient):
    """Testa criação e edição de evento com vínculo de contato principal e participantes."""
    headers = {"X-User-Phone": "5511999990001"}

    # 1. Cria 2 contatos
    c1 = (await api_client.post("/api/v1/contacts", json={"name": "Dra. Mariana", "phone": "5511988881111", "email": "mariana@clinica.com"}, headers=headers)).json()
    c2 = (await api_client.post("/api/v1/contacts", json={"name": "Carlos Assistente", "phone": "5511988882222", "email": "carlos@clinica.com"}, headers=headers)).json()

    # 2. Cria evento com c1 como main_contact e c2 como participante
    start_dt = datetime(2026, 12, 1, 14, 0, tzinfo=SAO_PAULO_TZ)
    end_dt = datetime(2026, 12, 1, 15, 0, tzinfo=SAO_PAULO_TZ)
    payload = {
        "title": "Cirurgia Eletiva",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "location": "Hospital São Paulo",
        "main_contact_id": c1["id"],
        "participant_ids": [c2["id"]],
    }
    res_create = await api_client.post("/api/v1/events", json=payload, headers=headers)
    assert res_create.status_code == 201
    ev_data = res_create.json()
    assert ev_data["main_contact_id"] == c1["id"]
    assert ev_data["contact_id"] == c1["id"]
    assert c2["id"] in ev_data["participant_ids"]

    # 3. Edita o evento via PATCH /api/v1/events/{id}: altera título e adiciona c1 também aos participantes
    patch_payload = {
        "title": "Cirurgia Eletiva Atualizada",
        "participant_ids": [c1["id"], c2["id"]],
    }
    res_patch = await api_client.patch(f"/api/v1/events/{ev_data['id']}", json=patch_payload, headers=headers)
    assert res_patch.status_code == 200
    patched_data = res_patch.json()
    assert patched_data["title"] == "Cirurgia Eletiva Atualizada"
    assert c1["id"] in patched_data["participant_ids"]
    assert c2["id"] in patched_data["participant_ids"]

