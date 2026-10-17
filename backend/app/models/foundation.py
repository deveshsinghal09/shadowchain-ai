from pydantic import AwareDatetime, BaseModel


class FoundationRecord(BaseModel):
    id: str
    schema_version: int
    created_at: AwareDatetime
