from pydantic import BaseModel, ConfigDict, Field


class AccountForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=100)
