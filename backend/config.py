import json
from pathlib import Path
from pydantic_settings import BaseSettings

CONFIG_FILE = Path(__file__).parent / "config.json"


class Settings(BaseSettings):
    knowledge_base_dir: str = str(Path(__file__).parent / "knowledge_base")
    db_path: str = str(Path(__file__).parent / "metadata.db")
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_model_fast: str = ""  # 路由快模型（简单类用），空=禁用路由全走 llm_model
    llm_api_format: str = "openai"  # "openai" or "anthropic"
    host: str = "0.0.0.0"
    port: int = 8000
    wecom_cli_path: str = ""  # 空=自动探测(PATH / %APPDATA%\npm\wecom-cli.cmd)


def load_settings() -> Settings:
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return Settings(**data)
    return Settings()


def save_settings(settings: Settings):
    CONFIG_FILE.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
