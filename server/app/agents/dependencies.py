from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.config import settings
from app.agents.agents_file import AgentRegistry

registry = AgentRegistry(settings.AGENTS_FILE)


async def require_agent(x_token: Annotated[str | None, Header()] = None) -> str:
    """Token menentukan nama container, jadi agen tidak bisa menulis ke folder container lain."""
    name = registry.authenticate(x_token)
    if name is None:
        raise HTTPException(status_code=401, detail="Token tidak dikenal.")
    return name


AgentName = Annotated[str, Depends(require_agent)]
