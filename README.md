## YouTube Shorts Upload Automation

Automates daily upload of **3 short videos** from a Google Drive folder and schedules them during peak audience watch hours.

### What it does
- Reads videos from a provided Google Drive folder.
- Skips files that were already uploaded (tracked by embedded `DriveFileId:<id>` marker in description).
- Uses YouTube Analytics (`views` by hour over recent days) to estimate top 3 audience hours.
- Uploads up to 3 new videos and schedules publishing at those peak times.

### Setup
1. Create OAuth client in Google Cloud and enable:
   - YouTube Data API v3
   - YouTube Analytics API
   - Google Drive API
2. Generate a refresh token for the same Google account/channel.
3. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Configure environment variables (copy `.env.example`).

### Run once
```bash
python uploader.py --drive-link "https://drive.google.com/drive/folders/<folder-id>"
```

Dry run:
```bash
python uploader.py --drive-link "<folder-id>" --dry-run
```

### Run daily (cron)
Run every day (example 08:00 server time):
```cron
0 8 * * * /path/to/python /workspace/shorts_upload_automation/uploader.py --drive-link "https://drive.google.com/drive/folders/<folder-id>" >> /var/log/shorts_uploader.log 2>&1
```

The script uploads max 3 new files per run; if fewer than 3 are new, it uploads only available new files.
