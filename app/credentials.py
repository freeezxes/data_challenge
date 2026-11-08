"""Deployment edition: credentials belong to one HTTP request, never the process."""
from contextvars import ContextVar
import re

from .store import StoreError

request_key = ContextVar("cqa_request_key", default="")
MISSING_KEY = "Добавьте свой ключ Claude API (ANTHROPIC_API_KEY) кнопкой «API-ключ» перед запуском"
MODEL_ROUTES = re.compile(
    r"/api/(?:jobs|operators/plan|copilot/(?:hint|client|finish)|"
    r"evaluations/\d+/(?:coach|variant)|appeals/\d+/assist)$"
)


def validate_key(value):
    if not value:
        return ""
    if not re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{20,500}", value):
        raise StoreError("Проверьте ключ Claude API: он должен начинаться с sk-ant- и не содержать пробелов")
    return value
