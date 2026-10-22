"""Atalho para iniciar o simulador interativo do Meu Assistente."""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Carrega explicitamente o .env da raiz antes de qualquer importação
ROOT_DIR = Path(__file__).resolve().parent
env_path = ROOT_DIR / ".env"
if env_path.is_file():
    load_dotenv(dotenv_path=env_path, override=True, encoding="utf-8-sig")
else:
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(dotenv_path=found, override=True, encoding="utf-8-sig")

from app.channels.simulator import main  # noqa: E402

if __name__ == "__main__":
    main()
