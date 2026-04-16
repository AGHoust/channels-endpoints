from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, date, datetime

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field


load_dotenv()

logger = logging.getLogger("mp_build_tracker")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _get_webhook_secret() -> str:
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("WEBHOOK_SECRET is not set")
    return secret


def require_webhook_secret(
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> None:
    expected = _get_webhook_secret()
    provided = x_webhook_secret or ""

    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


class MPBuildTrackerRow(BaseModel):
    home_code: str = Field(min_length=1)
    onboard_date: date
    booking_url: str = Field(default="")
    mp_active: bool
    sync_ready: bool
    photos_ready: bool


class MPBuildTrackerRequest(BaseModel):
    run_date: date
    source: str = Field(min_length=1)
    rows: list[MPBuildTrackerRow] = Field(default_factory=list)


class MPBuildTrackerResponse(BaseModel):
    status: str
    message: str
    processed_rows: int
    received_at: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "mp-build-tracker"


app = FastAPI(title="Internal Automations Service", version="1.0.0")


@app.on_event("startup")
def _startup_checks() -> None:
    _get_webhook_secret()
    logger.info("Service started")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.post(
    "/jobs/MPBuildTracker",
    response_model=MPBuildTrackerResponse,
    dependencies=[Depends(require_webhook_secret)],
)
def mp_build_tracker_job(payload: MPBuildTrackerRequest) -> MPBuildTrackerResponse:
    logger.info("RAW PAYLOAD: %s", payload.model_dump(mode="json"))
    sample = payload.rows[:3]
    logger.info(
        "Received MPBuildTracker job",
        extra={
            "run_date": payload.run_date.isoformat(),
            "source": payload.source,
            "rows_count": len(payload.rows),
            "rows_sample": [row.model_dump() for row in sample],
        },
    )

    # TODO: Google Sheets tracker logic will go here.
    # - Validate/transform rows as needed
    # - Write/update the tracking sheet
    # - Handle retries / partial failures

    received_at = datetime.now(UTC)
    return MPBuildTrackerResponse(
        status="ok",
        message="Job received",
        processed_rows=len(payload.rows),
        received_at=received_at,
    )
