## YouTube Shorts Upload Automation

Automates daily upload of **up to 3 short videos** from a Google Drive folder.

You now have **2 upload modes**:
- `immediate`: uploads and publishes videos right away.
- `analytics`: uploads and schedules videos at peak audience hours.

## What you need to change
1. Copy env template:
   ```bash
   cp .env.example .env
   ```
2. Fill your values in `.env`:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
   - `DRIVE_FOLDER_LINK`
3. Optional values:
   - `CHANNEL_TZ_OFFSET_HOURS`
   - `WORKDIR`
   - `PUBLISH_STRATEGY` (`analytics` or `immediate`)

## Prerequisites
- Python 3.9+
- Google Cloud APIs enabled:
  - YouTube Data API v3
  - YouTube Analytics API
  - Google Drive API
- OAuth client credentials + refresh token for the same Google account/channel.

## Install locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally
Load env first:
```bash
set -a && source .env && set +a
```

### Option 1: upload immediately
```bash
python uploader.py --publish-strategy immediate
```

### Option 2: use analytics and schedule uploads
```bash
python uploader.py --publish-strategy analytics
```

### Dry run (no upload)
```bash
python uploader.py --publish-strategy analytics --dry-run
```

## GitHub Actions workflow (manual run)
A manual workflow is included at `.github/workflows/upload-shorts.yml`.

### How to set env variables in GitHub
1. Open your repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Click **New repository secret** and create:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
   - `DRIVE_FOLDER_LINK`
   - `CHANNEL_TZ_OFFSET_HOURS` (optional)
4. Go to **Actions → Upload YouTube Shorts → Run workflow**.
5. Choose:
   - `publish_strategy = immediate` (publish now), or
   - `publish_strategy = analytics` (schedule at peak hours).
6. Keep `dry_run=true` for first test, then run with `dry_run=false`.

## Duplicate skipping behavior
- On upload, script appends `DriveFileId:<drive_file_id>` in description.
- On future runs, files with already-seen Drive IDs are skipped.

## Troubleshooting
- `Missing required environment variable`: ensure `.env` (local) or GitHub secrets are set.
- `No YouTube channel found`: refresh token account has no channel.
- `insufficientPermissions`: APIs/scopes are incomplete.
- `No new Drive videos to upload.`: all videos are already uploaded or folder has no videos.

- `unauthorized_client` (RefreshError): your OAuth client and refresh token do not match, or the OAuth app is still in testing without your account added as a test user. Regenerate the refresh token using the same `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, and ensure consent screen/app user access is configured.
