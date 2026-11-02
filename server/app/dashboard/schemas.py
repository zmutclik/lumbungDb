from pydantic import BaseModel, ConfigDict, Field


class ContainerForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    port: int = Field(default=3306, ge=1, le=65535)


class BackupRequestForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    what: str = Field(min_length=1, max_length=80)   # full | dump | dump:<database> | binlog
