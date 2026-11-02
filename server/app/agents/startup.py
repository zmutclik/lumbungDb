from app.agents.dependencies import registry


def on_startup() -> None:
    registry.load()
