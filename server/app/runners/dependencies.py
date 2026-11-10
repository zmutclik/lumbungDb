from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.agents.agents_file import AgentRegistry
from app.core.config import settings

registry = AgentRegistry(settings.RUNNERS_FILE)


async def require_runner(x_runner_token: Annotated[str | None, Header()] = None) -> str:
    """File dan header terpisah dari agen, jadi token agen tidak bisa mengambil tugas restore."""
    name = registry.authenticate(x_runner_token)
    if name is None:
        raise HTTPException(status_code=401, detail="Token runner tidak dikenal.")
    return name


RunnerName = Annotated[str, Depends(require_runner)]
