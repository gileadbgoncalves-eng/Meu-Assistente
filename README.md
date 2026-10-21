# 🤖 Meu Assistente (MVP de Agenda Pessoal via WhatsApp)

Assistente pessoal de rotina e compromissos operando principalmente via WhatsApp (ou pelo simulador interativo de linha de comando no terminal). O sistema utiliza **Google Gemini** para extração semântica profunda e um **motor determinístico de validação** com banco relacional e trilha de auditoria append-only.

---

## 🎯 Arquitetura e Princípios Centrais

1. **Separação Estrita de Camadas**:
   - A IA (`GeminiInterpreter`) é tratada como um motor semântico que produz saídas estruturadas estritas (`AIInterpretationResult` via Pydantic v2).
   - A IA **nunca** executa SQL nem altera diretamente o banco de dados.
2. **Validação Determinística**:
   - Fuso horário gerenciado via `zoneinfo` (`America/Sao_Paulo`) sem erros de virada de mês/ano ou ano bissexto.
   - Algoritmo matemático para detecção de sobreposição de intervalos semiabertos $[início, fim)$.
3. **Máquina de Estados e Confirmação Obrigatória**:
   - Operações sensíveis (conflitos de agenda, reagendamento ou cancelamento) exigem confirmação explícita (*"sim"*, *"pode agendar"*, *"beleza"*).
4. **Persistência Relacional e Auditoria Imutável**:
   - SQLAlchemy 2.0 Async + SQLite (dev/testes) e PostgreSQL com `asyncpg` (produção).
   - Tabela `change_log` append-only que registra o que mudou, quando, por quem e qual o motivo.
   - Tabela `message_processing` para idempotência absoluta de webhooks e mensagens repetidas.
5. **Portas Abertas para Futuras Integrações**:
   - Interfaces e adaptadores prontos para **Google Agenda** (`GoogleCalendarAdapter`) e **Google Maps Platform** (`GoogleMapsAdapter`).
6. **Canal WhatsApp Cloud API Oficial**:
   - Endpoint FastAPI com handshake Meta (`GET /webhook`), assinatura criptográfica `X-Hub-Signature-256` e despacho assíncrono em `BackgroundTasks` (`POST /webhook`).

---

## 🚀 Como Executar o Servidor FastAPI (Webhook WhatsApp)

Para iniciar o servidor FastAPI com suporte a Webhook da Meta:

```bash
# Inicia o servidor na porta 8000
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Documentação interativa Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- Endpoint de Webhook para a Meta: `http://seu-dominio.com/webhook`

---

## 💬 Como Executar o Simulador Interativo (CLI)

Você pode conversar no terminal simulando a troca de mensagens do WhatsApp:

```bash
# Modo com Gemini Real (utilizando a GEMINI_API_KEY do .env):
.\.venv\Scripts\python.exe simulator.py

# Modo Mock Offline (para testar rápido sem consumir API):
.\.venv\Scripts\python.exe simulator.py --mock
```

### Comandos Úteis no Simulador:
- `/agenda`: Lista todos os compromissos salvos no banco de dados.
- `/auditoria`: Exibe os últimos registros da trilha de auditoria (`change_log`).
- `/limpar`: Limpa o estado da conversa e propostas pendentes.
- `/ajuda`: Mostra a tela de ajuda com os comandos.
- `/sair`: Encerra a simulação.

---

## 🧪 Como Rodar a Suíte de Testes Automatizados

A suíte possui **38 testes automatizados** cobrindo isolamento de usuários, regras de conflito, viradas de mês/ano, feriados, mensagens ambíguas, injeção de prompt, idempotência, webhooks e adaptadores:

```bash
.\.venv\Scripts\pytest.exe -v
```
