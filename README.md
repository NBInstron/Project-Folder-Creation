# ERP Google Drive Project Folder Webhook

Flask webhook service that receives an ERP `AFTER_INSERT` event, validates `ProjectName`
and `Customer`, then creates the project folder tree in Google Drive using a service
account. After the folder tree is ready, it copies project-scope templates from
Google Shared Drive with the Drive API `files.copy` operation.

## Files

- `app.py` - Flask application factory, health check, and global error handlers.
- `routes/webhook.py` - HTTP POST webhook handler and payload validation.
- `services/drive_service.py` - Google Drive service-account authentication, idempotent folder creation, and Drive-side template copying.
- `services/folder_structure.py` - Folder hierarchy converted from the Windows batch file.
- `logging_config.py` - Console and rotating file logging.
- `config.py` - Environment-based configuration.

## Google Drive API Setup

1. Open Google Cloud Console and create or select a project.
2. Enable **Google Drive API** for that project.
3. Create a **Service Account**.
4. Create a JSON key for the service account and download it.
5. Rename the downloaded key to `credentials.json` and place it in this project root,
   or set `GOOGLE_APPLICATION_CREDENTIALS` to the full path of the JSON file.
6. Copy the service account email from the JSON file or Cloud Console.
7. Add the service account to the Shared Drive as a member with Contributor or
   Content manager access.
8. Keep `Projects` and `ITA-XXX_AI_Enebelment/Template` inside that Shared Drive.
9. The template folder must contain:
   - CLT template containing `CLT_03_R01` in the filename, such as `Template/01. <ProjectName>_CLT_03_R01.xlsx`
   - SOW template containing `SOW` in the filename, such as `Template/02. <ProjectName>_SOW.docx`
10. Set `GOOGLE_SHARED_DRIVE_ID`, `DRIVE_PROJECTS_FOLDER_ID`, and
    `DRIVE_TEMPLATE_FOLDER_ID`. The included defaults match the current Shared Drive:
    `0AJxQyDwRTE3HUk9PVA`, `1PXrPJgxcsz_R9e1i62kRxKG2TxfuI7HY`, and
    `1PbwV2FsD3GWIloH_nDYyy2jsCH90cHGP`.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` as needed:

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=credentials.json
GOOGLE_AUTH_MODE=service_account
GOOGLE_IMPERSONATED_USER=
GOOGLE_SHARED_DRIVE_ID=0AJxQyDwRTE3HUk9PVA
DRIVE_PROJECTS_FOLDER_ID=1PXrPJgxcsz_R9e1i62kRxKG2TxfuI7HY
DRIVE_TEMPLATE_ROOT_FOLDER_NAME=ITA-XXX_AI_Enebelment
DRIVE_TEMPLATE_ROOT_FOLDER_ID=
DRIVE_TEMPLATE_FOLDER_NAME=Template
DRIVE_TEMPLATE_FOLDER_ID=1PbwV2FsD3GWIloH_nDYyy2jsCH90cHGP
DEMO_PROJECTS_FOLDER_NAME=Projects
DEMO_PROJECTS_PARENT_ID=
WEBHOOK_TOKEN=change-this-token
WEBHOOK_ASYNC=true
FOLDER_JOB_WORKERS=2
PROCESSED_DIR=processed
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

On Render, leave the start command empty so Render uses `Procfile`, or set it to:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 --graceful-timeout 120 --access-logfile -
```

`/webhook/after-insert` starts the full Google Drive folder-tree creation as a
background job and returns `202 Accepted` with a `jobId`. Check the job state with:

```text
GET /webhook/jobs/<jobId>
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
  "status": "accepted",
  "message": "Project folder setup started",
  "jobId": "b0f353b68f69f11076c8c39498a4ebd2",
  "statusUrl": "/webhook/jobs/b0f353b68f69f11076c8c39498a4ebd2"
}
```

When the background job completes, `GET /webhook/jobs/<jobId>` includes Drive
folder details and counts of created/existing folders plus copied/existing
template files. Target files are created in `05. Project/03. Project Scope` as:

```text
01.<ProjectName>_CLT_03_R01.xlsm
02.<ProjectName>_SOW.docx
```
