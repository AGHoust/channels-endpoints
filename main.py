from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
import gspread
from google.oauth2.service_account import Credentials
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
    dependencies=[Depends(require_webhook_secret)],
)
def mp_build_tracker_job(payload: dict) -> dict:
    def split_list(value: str) -> list[str]:
        return [x.strip() for x in str(value).split(",")]

    def parse_bool_list(value: str) -> list[bool]:
        return [x.strip().lower() == "true" for x in split_list(value)]

    def parse_int_list(value: str) -> list[int]:
        parsed: list[int] = []
        for item in split_list(value):
            try:
                parsed.append(int(item))
            except ValueError:
                parsed.append(0)
        return parsed

    def today_ddmmyyyy() -> str:
        return datetime.now(ZoneInfo("Europe/London")).strftime("%d/%m/%Y")

    def is_blank(value: str | None) -> bool:
        v = str(value or "").strip().lower()
        return v in ("", "none", "null")

    def normalise_date_for_key(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        if isinstance(value, date):
            return value.strftime("%d/%m/%Y")

        raw = str(value).strip()
        if not raw:
            return None

        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%d/%m/%Y")
            except ValueError:
                pass

        # Handle datetime-like strings: "2026-04-16T00:00:00Z", etc.
        iso_candidate = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(iso_candidate).strftime("%d/%m/%Y")
        except ValueError:
            pass

        # Last resort: date part before a space.
        date_token = raw.split(" ")[0]
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(date_token, fmt).strftime("%d/%m/%Y")
            except ValueError:
                pass
        return None

    home_codes = split_list(payload.get("home_codes", ""))
    onboard_dates = split_list(payload.get("onboard_dates", ""))
    mp_active = parse_bool_list(payload.get("mp_active", ""))
    guesty_image_count = parse_int_list(payload.get("guesty_image_count", ""))
    booking_urls = split_list(payload.get("booking_urls", ""))
    homeaway_urls = split_list(payload.get("homeaway_urls", ""))
    hometogo_urls = split_list(payload.get("hometogo_urls", ""))
    googlevr_urls = split_list(payload.get("googlevr_urls", ""))
    houststay_urls = split_list(payload.get("houststay_urls", ""))

    field_lengths = {
        "home_codes": len(home_codes),
        "onboard_dates": len(onboard_dates),
        "mp_active": len(mp_active),
        "guesty_image_count": len(guesty_image_count),
        "booking_urls": len(booking_urls),
        "homeaway_urls": len(homeaway_urls),
        "hometogo_urls": len(hometogo_urls),
        "googlevr_urls": len(googlevr_urls),
        "houststay_urls": len(houststay_urls),
    }
    logger.info("Payload field lengths: %s", field_lengths)
    if len(set(field_lengths.values())) != 1:
        logger.error("Payload list-length mismatch: %s", field_lengths)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Payload field lengths mismatch", "lengths": field_lengths},
        )

    rows: list[dict[str, object]] = []
    skipped_rows = 0
    for i in range(len(home_codes)):
        home_code = home_codes[i].strip()
        onboard_date_normalized = normalise_date_for_key(onboard_dates[i])
        if not home_code or not onboard_dates[i].strip() or not onboard_date_normalized:
            skipped_rows += 1
            logger.warning(
                "Skipping row %s due to blank/invalid key values (home_code=%r, onboard_date=%r)",
                i,
                home_code,
                onboard_dates[i],
            )
            continue

        rows.append(
            {
                "home_code": home_code,
                "onboard_date": onboard_date_normalized,
                "images": guesty_image_count[i],
                "mp_active": mp_active[i],
                "booking_url": booking_urls[i].strip(),
                "homeaway_url": homeaway_urls[i].strip(),
                "hometogo_url": hometogo_urls[i].strip(),
                "googlevr_url": googlevr_urls[i].strip(),
                "houststay_url": houststay_urls[i].strip(),
            }
        )

    logger.info("Valid parsed rows: %s", len(rows))
    logger.info("Skipped rows: %s", skipped_rows)
    logger.info("Sample parsed row: %s", rows[0] if rows else "No rows")

    sheet_url = os.getenv("MP_TRACKER_SHEET_URL", "")
    if not sheet_url:
        raise RuntimeError("MP_TRACKER_SHEET_URL is not set")
    creds_json = os.getenv("GOOGLE_CREDS")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDS is not set")

    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    worksheet = client.open_by_url(sheet_url).worksheet("Sheet 19")

    # Read entire sheet into memory and build key lookup from tracker rows (start row 6).
    all_values = worksheet.get_all_values()
    start_row = 6
    lookup: dict[tuple[str, str], int] = {}
    skipped_sheet_rows = 0
    for row_idx in range(start_row, len(all_values) + 1):
        row = all_values[row_idx - 1]
        home_code = row[0].strip() if len(row) > 0 else ""
        onboard_date = row[1] if len(row) > 1 else ""
        onboard_date_normalized = normalise_date_for_key(onboard_date)
        if home_code and onboard_date_normalized:
            key = (home_code, onboard_date_normalized)
            if key in lookup:
                logger.warning(
                    "Duplicate sheet key detected for %s at row %s (previous row %s)",
                    key,
                    row_idx,
                    lookup[key],
                )
            lookup[key] = row_idx
        elif home_code or str(onboard_date).strip():
            skipped_sheet_rows += 1
    if skipped_sheet_rows:
        logger.warning(
            "Skipped %s sheet rows due to invalid/missing key date values",
            skipped_sheet_rows,
        )

    today = today_ddmmyyyy()

    def ensure_len(row: list[str], size: int) -> list[str]:
        if len(row) < size:
            row.extend([""] * (size - len(row)))
        return row

    def bool_to_sheet(value: bool) -> str:
        return "TRUE" if value else "FALSE"

    append_rows: list[list[str]] = []
    append_live_updates: list[dict[str, object]] = []
    existing_row_updates: list[dict[str, object]] = []
    existing_live_updates: list[dict[str, object]] = []

    new_rows_added = 0
    rows_updated = 0
    image_changes = 0
    url_updates = 0
    live_complete_dates_set = 0

    # Append rows start after the current last populated row.
    next_append_row = max(len(all_values), start_row - 1) + 1

    for row_data in rows:
        key = (str(row_data["home_code"]).strip(), str(row_data["onboard_date"]).strip())
        existing_row_number = lookup.get(key)

        if existing_row_number is None:
            bcom_url = str(row_data["booking_url"]).strip()
            vrbo_url = str(row_data["homeaway_url"]).strip()
            hometogo_url = str(row_data["hometogo_url"]).strip()
            gvr_url = str(row_data["googlevr_url"]).strip()
            houst_direct_url = str(row_data["houststay_url"]).strip()

            row_a_to_n = [
                str(row_data["home_code"]).strip(),        # A
                str(row_data["onboard_date"]).strip(),     # B (DD/MM/YYYY)
                str(row_data["images"]),                   # C
                bool_to_sheet(bool(row_data["mp_active"])),  # D
                bcom_url,                                  # E
                today if bcom_url else "",                 # F
                vrbo_url,                                  # G
                today if vrbo_url else "",                 # H
                hometogo_url,                              # I
                today if hometogo_url else "",             # J
                gvr_url,                                   # K
                today if gvr_url else "",                  # L
                houst_direct_url,                          # M
                today if houst_direct_url else "",         # N
            ]
            append_rows.append(row_a_to_n)
            new_rows_added += 1

            all_urls_filled = all([bcom_url, vrbo_url, hometogo_url, gvr_url, houst_direct_url])
            if all_urls_filled:
                append_live_updates.append(
                    {"range": f"V{next_append_row}", "values": [[today]]}
                )
                live_complete_dates_set += 1
            next_append_row += 1
            continue

        existing_raw_row = all_values[existing_row_number - 1] if existing_row_number - 1 < len(all_values) else []
        existing_row = ensure_len(list(existing_raw_row), 22)
        changed = False

        new_images = str(row_data["images"])
        if existing_row[2].strip() != new_images:
            existing_row[2] = new_images
            image_changes += 1
            changed = True

        new_mp_active = bool_to_sheet(bool(row_data["mp_active"]))
        if existing_row[3].strip().upper() != new_mp_active:
            existing_row[3] = new_mp_active
            changed = True

        for url_col, date_col, incoming_url in [
            (4, 5, str(row_data["booking_url"]).strip()),
            (6, 7, str(row_data["homeaway_url"]).strip()),
            (8, 9, str(row_data["hometogo_url"]).strip()),
            (10, 11, str(row_data["googlevr_url"]).strip()),
            (12, 13, str(row_data["houststay_url"]).strip()),
        ]:
            if is_blank(incoming_url):
                continue
            current_url = existing_row[url_col].strip()
            if is_blank(current_url):
                existing_row[url_col] = incoming_url
                url_updates += 1
                changed = True
                if is_blank(existing_row[date_col]):
                    existing_row[date_col] = today
            elif current_url != incoming_url:
                existing_row[url_col] = incoming_url
                url_updates += 1
                changed = True

        if changed:
            existing_row_updates.append(
                {
                    "range": f"A{existing_row_number}:N{existing_row_number}",
                    "values": [existing_row[:14]],
                }
            )
            rows_updated += 1

        all_urls_filled = all(
            [
                not is_blank(existing_row[4]),
                not is_blank(existing_row[6]),
                not is_blank(existing_row[8]),
                not is_blank(existing_row[10]),
                not is_blank(existing_row[12]),
            ]
        )
        if all_urls_filled and is_blank(existing_row[21]):
            existing_live_updates.append(
                {"range": f"V{existing_row_number}", "values": [[today]]}
            )
            live_complete_dates_set += 1

    if append_rows:
        worksheet.append_rows(append_rows, value_input_option="USER_ENTERED")
    if append_live_updates:
        worksheet.batch_update(append_live_updates, value_input_option="USER_ENTERED")
    if existing_row_updates:
        worksheet.batch_update(existing_row_updates, value_input_option="USER_ENTERED")
    if existing_live_updates:
        worksheet.batch_update(existing_live_updates, value_input_option="USER_ENTERED")

    logger.info("New rows added: %s", new_rows_added)
    logger.info("Rows updated: %s", rows_updated)
    logger.info("Image changes: %s", image_changes)
    logger.info("URL updates: %s", url_updates)
    logger.info("Live complete dates set: %s", live_complete_dates_set)

    return {
        "status": "ok",
        "rows_count": len(rows),
        "new_rows_added": new_rows_added,
        "rows_updated": rows_updated,
        "image_changes": image_changes,
        "url_updates": url_updates,
        "live_complete_dates_set": live_complete_dates_set,
    }
