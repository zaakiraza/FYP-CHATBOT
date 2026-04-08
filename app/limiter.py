import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_rpm = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
CHAT_RATE_LIMIT = f"{_rpm}/minute"

limiter = Limiter(key_func=get_remote_address)
