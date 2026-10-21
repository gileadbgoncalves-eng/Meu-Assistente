# 🤖 Meu Assistente (MVP de Agenda Pessoal via WhatsApp)

Assistente pessoal de rotina e compromissos operando principalmente via WhatsApp (ou pelo simulador interativo de linha de comando no terminal). O sistema utiliza **Google Gemini** para extração semântica profunda e um **motor determinístico de validação** com banco relacional e trilha de auditoria append-only.

---

## 🎯 Arquitetura e Princípios Centrais

1. **Separação Estrita de Camadas**:
   - A IA (`GeminiInterpreter`) é tratada como um motor semântico que produz saídas estruturadas estritas (`AIInterpretationResult` via Pydantic v2).
   - A IA **nunca** executa SQL nem altera diretamente o banco de dados.
2. **Validação Determinística**:
   - Fuso horário gerenciado via `zoneinfo` (`America/Sao_Paulo`) sem erros de virada de mês/ano.
   - Algoritmo matemático para detecção de sobreposição de intervalos semiabertos $[início, fim)$.
3. **Máquina de Estados e Confirmação Obrigatória**:
   - Operações sensíveis (conflitos de agenda, reagendamento ou cancelamento) exigem confirmação explícita (*"sim"*, *"pode agendar"*, *"beleza"*).
4. **Persistência Relacional e Auditoria Imutável**:
   - SQLAlchemy 2.0 Async + SQLite (dev/testes) e PostgreSQL (produção).
   - Tabela `change_log` append-only que registra o que mudou, quando, por quem e qual o motivo.
   - Tabela `message_processing` para idempotência absoluta de webhooks e mensagens repetidas.
5. **Portas Abertas para Futuras Integrações**:
   - Interfaces desacopladas prontas para **Google Agenda** (`CalendarSyncPort`) e **Google Maps Platform** (`TravelTimeEstimatorPort`).

---

## 🚀 Como Executar o Simulador Interativo (CLI)

### 1. Configurar Chave do Gemini (Opcional)
Se desejar usar o Gemini real, configure o arquivo `.env` a partir do template:
```bash
cp .env.example .env
```
E insira sua chave em `GEMINI_API_KEY`:
```env
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=gemini-2.5-flash
```

### 2. Iniciar o Simulador no Terminal
Abra seu terminal no diretório do projeto e execute:

```bash
# Modo com Gemini Real (se a GEMINI_API_KEY estiver configurada):
.\.venv\Scripts\python.exe simulator.py

# Modo Mock Offline (para testar rápido sem consumir API):
.\.venv\Scripts\python.exe simulator.py --mock
```

---

## 💬 Comandos Utilitários no Simulador

Durante a conversa no terminal, você pode digitar:
- `/agenda`: Lista todos os compromissos salvos no banco de dados.
- `/auditoria`: Exibe os últimos registros da trilha de auditoria (`change_log`).
- `/limpar`: Limpa o estado da conversa e propostas pendentes.
- `/ajuda`: Mostra a tela de ajuda.
- `/sair`: Encerra a simulação.

---

## 🧪 Como Rodar a Suíte de Testes Automatizados

A suíte possui **26 testes automatizados** cobrindo isolamento de usuários, regras de conflito, fusos horários, auditoria e fluxos conversacionais end-to-end:

```bash
.\.venv\Scripts\pytest.exe -v
```
