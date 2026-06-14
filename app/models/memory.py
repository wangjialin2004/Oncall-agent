from pydantic import BaseModel


class ExperienceMemoryUpdateRequest(BaseModel):
    enabled: bool
