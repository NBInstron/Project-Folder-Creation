# ERP Google Drive Project Folder Webhook

Flask webhook service that receives an ERP `AFTER_INSERT` event, validates `ProjectName`
and `Customer`, then creates the project folder tree in Google Drive using OAuth
user credentials.

## Files

- `app.py` - Flask application factory, health check, and global error handlers.
- `routes/webhook.py` - HTTP POST webhook handler and payload validation.
- `services/drive_service.py` - Google Drive authentication and idempotent folder creation.
- `services/folder_structure.py` - Folder hierarchy converted from the Windows batch file.
- `logging_config.py` - Console and rotating file logging.
- `config.py` - Environment-based configuration.

## Google Drive API Setup

1. Open Google Cloud Console and create or select a project.
2. Enable **Google Drive API** for that project.
3. Create an OAuth client for the Google account that owns or can edit the target
   Drive folder.
4. Generate an OAuth authorized-user token with the
   `https://www.googleapis.com/auth/drive` scope and save it as `oauth_token.json`.
   On Render, set `GOOGLE_OAUTH_TOKEN_JSON` to the full token JSON value instead
   of relying on a local file.
5. Optional but recommended: put your `Demo Projects` folder inside that shared
   parent and set `DEMO_PROJECTS_PARENT_ID` to the parent folder ID. If it is empty,
   the app searches all Drive locations visible to the OAuth user.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Generate the OAuth token locally:

```powershell
python scripts/generate_oauth_token.py --client-secrets client_secret.json --token-file oauth_token.json
```

Edit `.env` as needed:

```dotenv
OAUTH_TOKEN_FILE=oauth_token.json
GOOGLE_OAUTH_TOKEN_JSON=
DEMO_PROJECTS_FOLDER_NAME=Demo Projects
DEMO_PROJECTS_PARENT_ID=
WEBHOOK_TOKEN=change-this-token
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
LOG_FILE=logs/app.log
```

## Run

```powershell
flask --app app run --host 0.0.0.0 --port 5000
```

For production, run behind a reverse proxy and use a WSGI server:

```powershell
gunicorn "app:app" --bind 0.0.0.0:5000
```

On Windows production servers, use a Windows-compatible WSGI server or host the
service in a Linux container/VM.

## Test With Postman

1. Method: `POST`
2. URL: `http://localhost:5000/webhook/after-insert`
3. Headers:
   - `Content-Type: application/json`
   - `Authorization: Bearer change-this-token` if `WEBHOOK_TOKEN` is set.
4. Body, raw JSON:

```json
{
  "ProjectName": "ITP-123",
  "Customer": "AKOLA LTD"
}
```

Expected response:

```json
{
  "status": "success",
  "message": "Folder created"
}
```

The actual response also includes Drive folder IDs and counts of created/existing
folders.
