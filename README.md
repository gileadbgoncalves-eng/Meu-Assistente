# 🤖 Meu Assistente (v0.3.0-RC: Secretário Executivo & Agenda Inteligente)

Assistente pessoal de rotina, contatos e compromissos operando via **WhatsApp Cloud API**, **Painel Web Executivo** (`/painel`), **REST API v1** e **Simulador CLI Interativo**.

O sistema combina inteligência artificial generativa multimodal (**Google Gemini 2.5 Flash**) para extração semântica profunda e transcrição de voz com um **motor determinístico de regras de negócio**, detecção algorítmica de conflitos, análise de viabilidade logística de trânsito (**Google Maps**), sincronização híbrida com **Google Calendar** e persistência relacional com trilha de auditoria append-only.

---

## 🌟 Principais Recursos e Diferenciais (v0.3.0-RC)

1. **Mensagens de Áudio & IA Multimodal (WhatsApp)**:
   - Recebe áudios no WhatsApp, realiza download seguro e transcreve/interpreta via Gemini Multimodal diretamente no pipeline determinístico.
2. **Dashboard Web Embutido (`/painel`)**:
   - Interface Tailwind CSS moderna, responsiva e com tema escuro servida nativamente pelo FastAPI, sem dependências de build Node.js.
   - Cards de compromissos com indicador de deslocamento, modal de criação rápida com alerta de conflito em tempo real, agenda de contatos com busca instantânea e status de lembretes.
3. **API REST v1 Completa (`/api/v1/*`)**:
   - Endpoints completos para `events` (com validação e retorno HTTP 409 em sobreposições), `contacts` (CRUD completo), `reminders`, `briefing` e `status`.
   - Multi-tenant isolado por usuário (`X-User-Phone` ou query param, com fallback de desenvolvimento).
4. **Briefing Executivo Matinal ("Resumo do Dia")**:
   - Gera resumos diários elegantes e estruturados via comando conversacional (*"Resumo do meu dia"*, *"Briefing de hoje"*), pela API REST ou pelo Worker Assíncrono proativo.
5. **Inteligência Logística de Deslocamento (Google Maps)**:
   - Estimativa de tempo de trânsito e verificação de viabilidade entre compromissos sequenciais (`ConflictSeverity.LOGISTIC_RISK`), alertando o usuário e enriquecendo lembretes com dicas de trajeto (*"🚗 Deslocamento estimado: ~25 min"*).
6. **Sincronização Híbrida com Google Calendar**:
   - Integração bidirecional resiliente: opera com credenciais reais ou em modo de *Fallback Gracioso Local* com identificadores sintéticos (`gcal_local_*`), garantindo que o sistema nunca caia por instabilidade externa.
7. **Motor Determinístico & Confirmação Humana (Human-in-the-Loop)**:
   - Nenhuma decisão crítica ou gravação de conflito é feita pela IA. Conflitos de horário exigem confirmação explícita (*"sim"*, *"pode agendar"*).
8. **Persistência Relacional e Auditoria Imutável**:
   - SQLAlchemy 2.0 Async + SQLite (dev/testes) ou PostgreSQL (`asyncpg`).
   - Tabela `change_log` append-only para auditoria total e `message_processing` para idempotência absoluta.

---

## ⚡ Smoke Test de Integridade Sub-Segundo (< 0.1s)

Para validar a integridade de todos os 5 subsistemas em menos de 100 milissegundos:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

Saída esperada:
```
🔍 Iniciando Smoke Test do Meu Assistente (v0.3.0-RC)...
  [1/5] Verificando Banco de Dados, Contatos e Auditoria... OK ✅
  [2/5] Verificando Motor Determinístico e Logística de Trânsito... OK ✅
  [3/5] Verificando Camada de IA e Esquemas Tipados... OK ✅
  [4/5] Verificando Formatador de Briefing Executivo... OK ✅
  [5/5] Verificando Endpoints FastAPI, Webhook e Web API... OK ✅
--------------------------------------------------------------------------------
✨ Smoke Test concluído com sucesso em 0.095s! Todos os 5 subsistemas operacionais.
```

---

## 🖥️ Como Acessar o Dashboard Web e a API

Inicie o servidor local:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Painel Web Executivo**: [http://localhost:8000/painel](http://localhost:8000/painel) (ou `/dashboard`)
- **Documentação Interativa Swagger**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API REST de Eventos**: `GET/POST /api/v1/events`
- **API REST de Contatos**: `GET/POST/PUT/DELETE /api/v1/contacts`
- **API REST de Briefing**: `GET /api/v1/briefing`
- **Webhook WhatsApp**: `GET/POST /webhook`

---

## 💬 Como Usar o Simulador CLI no Terminal

O simulador de terminal permite testar toda a experiência conversacional sem depender da Meta:

```powershell
# Modo Mock Offline (rápido, sem consumo de API do Gemini):
.\.venv\Scripts\python.exe -m app.channels.simulator --mock

# Modo com Gemini Real (requer GEMINI_API_KEY no .env):
.\.venv\Scripts\python.exe -m app.channels.simulator
```

### Comandos Rápidos Disponíveis no CLI:
- `/agenda` → Lista compromissos salvos no banco.
- `/contatos` → Lista os contatos salvos.
- `/briefing` → Exibe o resumo executivo matinal do dia.
- `/lembretes` → Exibe os lembretes programados.
- `/painel` → Exibe a URL do Painel Web Executivo.
- `/auditoria` → Exibe o histórico de alterações (`change_log`).
- `/limpar` → Reseta as propostas pendentes da sessão.
- `/sair` → Encerra o simulador.

### Exemplos de Interações Conversacionais:
- *"Dentista amanhã às 14h na Av Paulista 1000"* (Criação de evento com local).
- *"Salvar contato Dr. Roberto 21999998888"* (Cadastro rápido de contato).
- *"Qual o telefone do Dr. Roberto?"* (Consulta inteligente de contato).
- *"Resumo do meu dia"* (Briefing executivo com tempos de deslocamento e horários).
- *"Me lembre desse compromisso 2h antes"* (Lembrete relativo anafórico).

---

## 🧪 Suíte de Testes Automatizados (Pytest)

A suíte conta com **97 testes automatizados** (100% assíncronos e isolados em memória):

```powershell
.\.venv\Scripts\pytest.exe -v
```

Verificação de estilo e linter (Ruff):
```powershell
.\.venv\Scripts\ruff.exe check .
```
