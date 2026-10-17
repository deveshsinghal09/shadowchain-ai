from datetime import UTC, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.core.time import utc_now
from app.models.graph import GraphModel
from app.models.memory import Key
from app.schemas.risk import GraphID


class AwsSyncRequest(GraphModel):
    request_id: Key
    confirm_account_id: Annotated[str, Field(pattern=r"^\d{12}$")]


class CloudTrailRequest(AwsSyncRequest):
    start_time: AwareDatetime
    end_time: AwareDatetime

    @model_validator(mode="after")
    def valid_window(self):
        now = utc_now()
        if not now - timedelta(days=90) <= self.start_time < self.end_time <= now:
            raise ValueError("Event History window must fall within the past 90 days")
        object.__setattr__(self, "start_time", self.start_time.astimezone(UTC))
        object.__setattr__(self, "end_time", self.end_time.astimezone(UTC))
        return self


class IamSyncV1(AwsSyncRequest):
    kind: Literal["iam"]


class CloudTrailSyncV1(CloudTrailRequest):
    kind: Literal["cloudtrail"]


AwsSyncV1 = Annotated[IamSyncV1 | CloudTrailSyncV1, Field(discriminator="kind")]


class SyncResult(GraphModel):
    run_id: GraphID
    kind: Literal["iam", "cloudtrail"]
    status: Literal["COMPLETE"] = "COMPLETE"
    node_count: int
    relationship_count: int
    coverage: dict
    limitations: list[str]


class ReportRequest(GraphModel):
    request_id: Key
    investigation_id: GraphID
    format: Literal["json"] = "json"


class ReportResult(GraphModel):
    report_id: GraphID
    status: Literal["PREPARED", "PUBLISHED"]
    export_mode: Literal["local", "s3"]
    content_sha256: str
    byte_count: int
    media_type: Literal["application/json"] = "application/json"
    artifact_uri: str
    redaction_version: Literal["allowlist-summary-v1"] = "allowlist-summary-v1"
