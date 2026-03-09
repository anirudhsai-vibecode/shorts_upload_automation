#!/usr/bin/env python3
"""Automate daily YouTube Shorts uploads from a Google Drive folder.

Features:
- Selects up to 3 not-yet-uploaded videos from a Drive folder.
- Detects already-uploaded files using a Drive file ID marker in the video description.
- Uses YouTube Analytics data to find audience peak hours and schedules uploads there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

DRIVE_LINK_RE = re.compile(r"(?:folders|d)/([a-zA-Z0-9_-]+)")
DRIVE_MARKER = "DriveFileId"


@dataclass
class DriveVideo:
    file_id: str
    name: str
    download_url: str
    created_time: str


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def int_env(name: str, fallback: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    raw = raw.strip()
    if not raw:
        return fallback
    return int(raw)


def get_credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=env("GOOGLE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=env("GOOGLE_CLIENT_ID"),
        client_secret=env("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )


def extract_drive_folder_id(link: str) -> str:
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", link):
        return link
    m = DRIVE_LINK_RE.search(link)
    if not m:
        raise ValueError("Could not extract Google Drive folder ID from DRIVE_FOLDER_LINK")
    return m.group(1)


def validate_credentials(creds: Credentials) -> None:
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        message = str(exc)
        if "unauthorized_client" in message:
            raise RuntimeError(
                "OAuth refresh token rejected (unauthorized_client). "
                "Check that GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET match the client used to generate "
                "GOOGLE_REFRESH_TOKEN, OAuth consent screen is configured, and the app is in Production "
                "or your Google account is added as a Test User."
            ) from exc
        raise RuntimeError(f"Failed to refresh Google OAuth token: {exc}") from exc


def build_services(creds: Credentials):
    youtube = build("youtube", "v3", credentials=creds)
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return youtube, analytics, drive


def get_uploads_playlist_id(youtube) -> str:
    resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for authenticated account")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_uploaded_drive_ids(youtube) -> Set[str]:
    uploads_id = get_uploads_playlist_id(youtube)
    video_ids: List[str] = []
    page_token = None
    while True:
        resp = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=uploads_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        video_ids.extend(
            item["contentDetails"]["videoId"]
            for item in resp.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    uploaded_ids: Set[str] = set()
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        if not chunk:
            continue
        details = youtube.videos().list(part="snippet", id=",".join(chunk), maxResults=50).execute()
        for item in details.get("items", []):
            description = item.get("snippet", {}).get("description", "")
            marker_match = re.search(rf"{DRIVE_MARKER}:([a-zA-Z0-9_-]+)", description)
            if marker_match:
                uploaded_ids.add(marker_match.group(1))
    return uploaded_ids


def list_drive_videos(drive, folder_id: str) -> List[DriveVideo]:
    files: List[DriveVideo] = []
    page_token = None
    query = (
        f"'{folder_id}' in parents and trashed=false "
        "and mimeType contains 'video/'"
    )
    while True:
        resp = (
            drive.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, createdTime, webContentLink)",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        for f in resp.get("files", []):
            files.append(
                DriveVideo(
                    file_id=f["id"],
                    name=f["name"],
                    created_time=f.get("createdTime", ""),
                    download_url=f.get("webContentLink", ""),
                )
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return sorted(files, key=lambda x: x.created_time)


def aggregate_peak_hours(analytics, days: int = 28) -> List[int]:
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)

    resp = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views",
            dimensions="day,hour",
            sort="-views",
            maxResults=200,
        )
        .execute()
    )

    hourly: Dict[int, int] = {h: 0 for h in range(24)}
    for row in resp.get("rows", []):
        # [day(YYYY-MM-DD), hour, views]
        hour = int(row[1])
        views = int(float(row[2]))
        hourly[hour] += views

    ranked = sorted(hourly.items(), key=lambda kv: kv[1], reverse=True)
    top = [h for h, _ in ranked[:3] if _ > 0]
    return top or [12, 17, 21]


def next_publish_times(hours: Sequence[int], tz_offset_hours: int = 0) -> List[dt.datetime]:
    now = dt.datetime.utcnow() + dt.timedelta(hours=tz_offset_hours)
    today = now.date()
    times: List[dt.datetime] = []

    for h in hours:
        candidate = dt.datetime.combine(today, dt.time(hour=h, minute=0, second=0))
        if candidate <= now + dt.timedelta(minutes=10):
            candidate += dt.timedelta(days=1)
        times.append(candidate - dt.timedelta(hours=tz_offset_hours))

    return sorted(times)


def download_drive_file(drive, file_id: str, out_path: str) -> None:
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(out_path, "wb") as fh:
        downloader = __import__(
            "googleapiclient.http", fromlist=["MediaIoBaseDownload"]
        ).MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_video(
    youtube,
    path: str,
    title: str,
    description: str,
    publish_at_utc: Optional[dt.datetime],
    tags: Optional[List[str]] = None,
) -> str:
    status: Dict[str, object] = {
        "selfDeclaredMadeForKids": False,
    }
    if publish_at_utc is None:
        status["privacyStatus"] = "public"
    else:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at_utc.replace(microsecond=0).isoformat() + "Z"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or ["shorts", "youtube shorts"],
            "categoryId": "22",
        },
        "status": status,
    }

    media = MediaFileUpload(path, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def select_new_videos(
    drive_videos: Iterable[DriveVideo], uploaded_drive_ids: Set[str], limit: int = 3
) -> List[DriveVideo]:
    selected = [v for v in drive_videos if v.file_id not in uploaded_drive_ids]
    return selected[:limit]


def run(
    drive_link: str,
    tz_offset_hours: int,
    workdir: str,
    publish_strategy: str,
    dry_run: bool = False,
) -> None:
    os.makedirs(workdir, exist_ok=True)
    creds = get_credentials()
    validate_credentials(creds)
    youtube, analytics, drive = build_services(creds)

    folder_id = extract_drive_folder_id(drive_link)
    uploaded_drive_ids = list_uploaded_drive_ids(youtube)
    drive_videos = list_drive_videos(drive, folder_id)
    to_upload = select_new_videos(drive_videos, uploaded_drive_ids, limit=3)

    if not to_upload:
        logging.info("No new Drive videos to upload.")
        return

    publish_times: List[Optional[dt.datetime]] = []
    if publish_strategy == "analytics":
        peak_hours = aggregate_peak_hours(analytics)
        publish_times = next_publish_times(peak_hours, tz_offset_hours=tz_offset_hours)

    for i, video in enumerate(to_upload):
        local_path = os.path.join(workdir, f"{video.file_id}_{video.name}")
        publish_at: Optional[dt.datetime] = None
        if publish_strategy == "analytics":
            publish_at = publish_times[i % len(publish_times)] + dt.timedelta(days=i // len(publish_times))
        marker = f"\n\n{DRIVE_MARKER}:{video.file_id}"
        schedule_line = (
            f"\nScheduled at: {publish_at.isoformat()} UTC"
            if publish_at is not None
            else "\nPublished immediately"
        )
        description = "Uploaded by shorts automation." f"\nOriginal file: {video.name}" + schedule_line + marker

        if dry_run:
            logging.info(
                "[DRY RUN] Would upload '%s' (Drive ID: %s) with strategy=%s %s",
                video.name,
                video.file_id,
                publish_strategy,
                f"at {publish_at.isoformat()}Z" if publish_at is not None else "immediately",
            )
            continue

        logging.info("Downloading %s", video.name)
        download_drive_file(drive, video.file_id, local_path)

        try:
            logging.info("Uploading %s", video.name)
            uploaded_video_id = upload_video(
                youtube=youtube,
                path=local_path,
                title=os.path.splitext(video.name)[0][:95],
                description=description,
                publish_at_utc=publish_at,
                tags=["shorts", "ytshorts", "automation"],
            )
            logging.info("Uploaded as video ID: %s", uploaded_video_id)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drive-link",
        default=os.getenv("DRIVE_FOLDER_LINK", ""),
        help="Google Drive folder link or folder ID",
    )
    parser.add_argument(
        "--tz-offset-hours",
        default=int_env("CHANNEL_TZ_OFFSET_HOURS", 0),
        type=int,
        help="Channel timezone offset from UTC (e.g. +5 for UTC+5)",
    )
    parser.add_argument(
        "--workdir",
        default=os.getenv("WORKDIR", "/tmp/shorts_uploads"),
        help="Temp folder for downloading videos before upload",
    )
    parser.add_argument(
        "--publish-strategy",
        default=os.getenv("PUBLISH_STRATEGY", "analytics"),
        choices=["analytics", "immediate"],
        help="'analytics' schedules by peak hours, 'immediate' publishes right away",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview actions only")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if not args.drive_link:
        print("Provide --drive-link or set DRIVE_FOLDER_LINK", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        run(
            drive_link=args.drive_link,
            tz_offset_hours=args.tz_offset_hours,
            workdir=args.workdir,
            publish_strategy=args.publish_strategy,
            dry_run=args.dry_run,
        )
    except HttpError as exc:
        logging.error("Google API error: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        logging.exception("Unhandled error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
