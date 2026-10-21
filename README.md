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

## ⚡ Smoke Test de Integridade (2 Segundos)

Para verificar rapidamente se todos os subsistemas (IA, Banco, Motor de Regras e Webhook) estão operacionais antes de iniciar:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

Saída esperada:
```
🔍 Iniciando Smoke Test do Meu Assistente...
  [1/4] Verificando Banco de Dados e Auditoria... OK ✅
  [2/4] Verificando Motor Determinístico de Regras e Conflitos... OK ✅
  [3/4] Verificando Camada de IA e Esquemas Tipados... OK ✅
  [4/4] Verificando Endpoints FastAPI e Webhook WhatsApp... OK ✅
--------------------------------------------------------------------------------
🚀 [STATUS]: Todos os subsistemas (IA, Banco, Motor de Regras, Webhook) estão operacionais e prontos para teste.
```

---

## 📋 Roteiro de Validação do Usuário (Passo a Passo)

Siga este roteiro prático no PowerShell para experimentar todos os recursos do assistente de ponta a ponta:

### 1. Iniciar o Simulador Conversacional
No terminal PowerShell, execute:

```powershell
# Com Gemini Real (requer GEMINI_API_KEY no .env):
.\.venv\Scripts\python.exe simulator.py

# Ou no modo Mock Offline (rápido e sem gastar cota):
.\.venv\Scripts\python.exe simulator.py --mock
```

### 2. Cenários de Teste Sugeridos (Envie no prompt do simulador)

#### Cenário A: Criação Direta sem Conflito
> **Você digita**: `Dentista amanhã às 14h na Rua Oscar Freire`  
> **Resultado esperado**: O assistente agenda o evento diretamente e exibe o resumo com data, horário (14h às 15h) e local.

#### Cenário B: Detecção de Conflito Proposital com Confirmação
> **Você digita**: `Reunião de equipe amanhã às 14h30`  
> **Resultado esperado**: O assistente **não grava de imediato**, identifica o conflito com a consulta do dentista e pergunta: *"Aviso de conflito: você já tem 'Dentista' marcado das 14h às 15h... Deseja manter mesmo com a sobreposição ou prefere outro horário?"*  
> **Você confirma**: `sim, pode agendar`  
> **Resultado esperado**: O assistente confirma a gravação de ambos os compromissos no banco.

#### Cenário C: Ambiguidade Severa (Sem Horário)
> **Você digita**: `Consulta com nutricionista na sexta-feira`  
> **Resultado esperado**: O assistente **não adivinha** o horário. Ele pergunta: *"Qual é o horário da sua consulta na sexta-feira?"*

#### Cenário D: Consulta de Agenda
> **Você digita**: `O que tenho amanhã?`  
> **Resultado esperado**: O assistente lista os compromissos cadastrados para o dia seguinte de forma limpa.

### 3. Comandos Utilitários no Terminal
- `/agenda` → Exibe em formato tabular todos os eventos gravados no banco relacional.
- `/auditoria` → Exibe os registros imutáveis de `change_log` comprovando que toda criação/atualização possui rastreabilidade.
- `/limpar` → Reseta as propostas pendentes da conversa.
- `/sair` → Finaliza a sessão do simulador.

---

## 🚀 Como Executar o Servidor FastAPI (Webhook WhatsApp)

Para receber mensagens reais via WhatsApp Cloud API (Meta for Developers):

```powershell
# Inicia o servidor ASGI na porta 8000
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Documentação Swagger interativa: [http://localhost:8000/docs](http://localhost:8000/docs)
- Endpoint de Webhook para a Meta: `http://seu-dominio.com/webhook`

---

## 🧪 Como Rodar a Suíte Completa de Testes

A suíte possui **38 testes automatizados** cobrindo isolamento de usuários, regras de conflito, viradas de mês/ano, anos bissextos, mensagens ambíguas, injeção de prompt, idempotência, webhooks e adaptadores externos:

```powershell
.\.venv\Scripts\pytest.exe -v
```
