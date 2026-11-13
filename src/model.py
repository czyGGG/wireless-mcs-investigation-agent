"""DeepSeek configuration shared by the project's agent entry points."""

import os

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from .paths import PROJECT_ROOT


class DeepSeekConfig(BaseModel):
    api_key: SecretStr = Field(min_length=1)
    model_name: str = Field(default="deepseek-flash", min_length=1)
    thinking: bool = False


def load_config() -> DeepSeekConfig:
    """Read the project .env, with process environment variables taking precedence."""
    values = dotenv_values(PROJECT_ROOT / ".env", interpolate=False)
    return DeepSeekConfig(
        api_key=os.environ.get("DEEPSEEK_API_KEY", values.get("DEEPSEEK_API_KEY") or ""),
        model_name=os.environ.get(
            "DEEPSEEK_MODEL", values.get("DEEPSEEK_MODEL") or "deepseek-flash"
        ),
        thinking=os.environ.get(
            "DEEPSEEK_THINKING", values.get("DEEPSEEK_THINKING") or "false"
        ),
    )


def create_model(config: DeepSeekConfig) -> OpenAIChatModel:
    return OpenAIChatModel(
        config.model_name,
        provider=DeepSeekProvider(api_key=config.api_key.get_secret_value()),
    )
