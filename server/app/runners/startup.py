from app.runners.dependencies import registry


def on_startup() -> None:
    registry.load()
