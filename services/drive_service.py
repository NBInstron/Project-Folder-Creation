import logging
import mimetypes
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import json


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveAuthError(RuntimeError):
    pass


class DriveService:
    def __init__(self, config: Any):
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._service = self._build_service(config.credentials_file)

    def upload_file_if_not_exists(
        self,
        local_path: Path,
        parent_id: str,
        target_name: str | None = None,
        fallback_parent_id: str | None = None,
    ) -> dict | None:
        """Upload a local file to Drive under parent_id if a file with same name doesn't exist.

        Returns the file resource when uploaded or the existing file resource when skipped.
        """
        if not local_path.exists() or not local_path.is_file():
            self._logger.warning("Local template not found, skipping: %s", local_path)
            return None

        name = target_name or local_path.name
        # Check if file with same name exists under the parent
        try:
            escaped_parent = self._escape_query_value(parent_id)
        except Exception:
            escaped_parent = parent_id.replace("'", "\\'")

        escaped_name = self._escape_query_value(name)
        query = f"name='{escaped_name}' and '{escaped_parent}' in parents and trashed=false"
        resp = (
            self._service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name, mimeType, webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1,
            )
            .execute()
        )
        files = resp.get("files", [])
        if files:
            existing = files[0]
            existing["_was_uploaded"] = False
            self._logger.info("File exists, skipping upload name=%s id=%s", name, existing.get("id"))
            return existing

        # Upload
        mime_type, _ = mimetypes.guess_type(str(local_path))
        media = MediaFileUpload(str(local_path), mimetype=mime_type or "application/octet-stream", resumable=False)
        metadata = {"name": name, "parents": [parent_id]}
        try:
            uploaded = (
                self._service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id, name, mimeType, webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            uploaded["_was_uploaded"] = True
            self._logger.info("Uploaded file name=%s id=%s", name, uploaded.get("id"))
            return uploaded
        except HttpError as he:
            # Common issue for service accounts: no user Drive storage quota.
            # Surface a clearer log message to help operators choose OAuth or Shared Drives.
            try:
                content = he.content.decode() if hasattr(he, "content") else str(he)
            except Exception:
                content = str(he)
            # Detect common storage-quota error and log a clearer message
            if isinstance(content, str) and "storageQuotaExceeded" in content:
                self._logger.error(
                    "Failed to upload file %s to parent=%s: storageQuotaExceeded. "
                    "This often happens when using a service account without Drive storage or when the target account is out of space. "
                    "Consider using OAuth user credentials or a Shared Drive.",
                    name,
                    parent_id,
                )
            else:
                self._logger.error(
                    "Failed to upload file %s to parent=%s: %s",
                    name,
                    parent_id,
                    content,
                )
            # If a fallback parent (e.g., a Shared Drive folder) is configured,
            # attempt a single retry there. Pass None as the fallback_parent_id
            # to avoid recursive retries.
            if fallback_parent_id and fallback_parent_id != parent_id:
                self._logger.info(
                    "Attempting upload to fallback parent=%s for file=%s",
                    fallback_parent_id,
                    name,
                )
                try:
                    fallback_res = self.upload_file_if_not_exists(local_path, fallback_parent_id, target_name or name, None)
                    if fallback_res and not fallback_res.get("_upload_failed"):
                        fallback_res["_was_uploaded_via_fallback"] = True
                        return fallback_res
                except Exception as e:
                    self._logger.exception("Fallback upload attempt failed: %s", e)

            # Return a structured failure so callers can distinguish
            # between a missing local file (None) and an upload failure.
            return {"_upload_failed": True, "error": content}

    def ensure_project_scope_templates(self, project_folder_id: str, template_dir: Path | None = None) -> dict[str, int]:
        """Ensure template folders and files exist under:
        <ProjectFolder> -> 05. Project -> 03. Project Scope -> [01.<Project>_CLT_03_R01, 02.<Project>_SOW]

        - `project_folder_id` : ID of the main project folder (My Drive/Projects/<Project Folder>)
        - `template_dir` : local Path to templates (defaults to workspace Templates/)

        Returns a summary dict with counts for folders created, existing, files uploaded/skipped.
        """
        summary = {
            "folders_created": 0,
            "folders_existing": 0,
            "files_uploaded": 0,
            "files_skipped": 0,
        }

        # Resolve templates directory
        if template_dir is None:
            template_dir = Path(__file__).resolve().parents[1] / "Templates"
        if not template_dir.exists() or not template_dir.is_dir():
            self._logger.error("Template directory not found: %s", template_dir)
            return summary

        # Resolve project name from project folder metadata
        try:
            proj_meta = self._service.files().get(fileId=project_folder_id, fields="id,name").execute()
            proj_full_name = proj_meta.get("name", "Project").strip()
            # Use the full project folder name as the project identifier
            project_name = proj_full_name
        except Exception as e:
            self._logger.error("Unable to fetch project folder metadata: %s", e)
            return summary

        # Ensure 05. Project under project folder
        created_map: dict = {}
        project_folder = get_or_create_folder(
            service=self._service,
            name="05. Project",
            parent_id=project_folder_id,
            logger=self._logger,
            project_key=None,
            created_map=created_map,
        )
        if project_folder.get("_was_created"):
            summary["folders_created"] += 1
        else:
            summary["folders_existing"] += 1

        # Ensure 03. Project Scope under 05. Project
        project_scope = get_or_create_folder(
            service=self._service,
            name="03. Project Scope",
            parent_id=project_folder["id"],
            logger=self._logger,
            project_key=None,
            created_map=created_map,
        )
        if project_scope.get("_was_created"):
            summary["folders_created"] += 1
            self._logger.info("03. Project Scope created id=%s for project=%s", project_scope.get("id"), proj_full_name)
        else:
            summary["folders_existing"] += 1
            self._logger.info("03. Project Scope already exists id=%s for project=%s", project_scope.get("id"), proj_full_name)

        # Upload only the two required templates into 03. Project Scope
        # Target filenames:
        #  - 01.<ProjectName>_CLT_03_R01.xlsx
        #  - 02.<ProjectName>_SOW.docx
        templates_to_find = {
            "clt": {
                "match_keywords": ["clt", "03_r01", "03-"],
                "ext": ".xlsx",
                "target": f"01.{project_name}_CLT_03_R01.xlsx",
            },
            "sow": {
                "match_keywords": ["sow"],
                "ext": ".docx",
                "target": f"02.{project_name}_SOW.docx",
            },
        }

        # Build a map of local template files by keyword
        found_templates = {"clt": None, "sow": None}
        # Two-pass approach: prefer files that include a project-name placeholder
        placeholder_keys = ("projectname", "project_name", "{projectname}", "{project_name}", "projectname")
        candidates: dict[str, list[Path]] = {"clt": [], "sow": []}

        for local in sorted(template_dir.iterdir()):
            if not local.is_file():
                continue
            lname = local.name.lower()
            for key, spec in templates_to_find.items():
                if spec["ext"] and not lname.endswith(spec["ext"]):
                    # still allow non-exact extension matches in candidates but prefer exact
                    pass
                if any(k in lname for k in spec["match_keywords"]):
                    candidates[key].append(local)

        # prefer a candidate that contains a project-name placeholder; otherwise pick first candidate
        for key in ("clt", "sow"):
            picked = None
            for c in candidates.get(key, []):
                lname = c.name.lower()
                if any(pk in lname for pk in placeholder_keys):
                    picked = c
                    break
            if not picked and candidates.get(key):
                picked = candidates[key][0]
            found_templates[key] = picked

        # Upload each required template if found
        for key, spec in templates_to_find.items():
            local = found_templates.get(key)
            target_name = spec["target"]
            if not local:
                self._logger.warning("Template for %s not found in %s; expected pattern keywords=%s", key, template_dir, spec["match_keywords"]) 
                summary["files_skipped"] += 1
                continue

            try:
                res = self.upload_file_if_not_exists(
                    local, project_scope["id"], target_name, getattr(self._config, "shared_drive_id", None)
                )
                if res is None:
                    # upload_file_if_not_exists returns None only when local file missing
                    self._logger.warning("Template file missing locally: %s", local)
                    summary["files_skipped"] += 1
                    continue
                if res.get("_upload_failed"):
                    # Upload attempted but failed (e.g., storage quota or permissions)
                    summary.setdefault("errors", 0)
                    summary["errors"] += 1
                    self._logger.warning("Failed to upload template %s: %s", target_name, res.get("error"))
                    continue

                if res and res.get("_was_uploaded"):
                    summary["files_uploaded"] += 1
                    self._logger.info("Uploaded template %s as %s into 03. Project Scope (id=%s)", local.name, target_name, project_scope.get("id"))
                elif res:
                    summary["files_skipped"] += 1
                    self._logger.info("Skipped upload (already exists) %s in 03. Project Scope (id=%s)", target_name, project_scope.get("id"))
            except Exception as e:
                self._logger.exception("Failed to upload template %s to 03. Project Scope: %s", target_name, e)
                # continue with other templates
                summary.setdefault("errors", 0)
                summary["errors"] += 1

        return summary

    def _build_service(self, credentials_file: Path):
        # Use OAuth InstalledAppFlow (user account) to authenticate to Google Drive.
        # This replaces service-account authentication so uploads go into the end-user's My Drive.
        oauth_secrets = getattr(self._config, "oauth_client_secrets", "client_secret.json")
        oauth_token_file = getattr(self._config, "oauth_token_file", "oauth_token.json")

        client_path = Path(oauth_secrets)
        token_path = Path(oauth_token_file)

        if not client_path.exists():
            raise DriveAuthError(
                f"OAuth client secrets not found: {client_path}. Place client_secret.json in the project root or set `oauth_client_secrets` in config."
            )

        try:
            creds = None
            # Try loading existing token
            if token_path.exists():
                try:
                    creds = Credentials.from_authorized_user_file(str(token_path), scopes=DRIVE_SCOPES)
                except Exception:
                    creds = None

            # If no valid credentials, run the InstalledAppFlow
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=DRIVE_SCOPES)
                    # `run_local_server` prints an authorization URL prompt. We set a clear message to surface it.
                    auth_prompt = (
                        "Please visit this URL to authorize access to your Google Drive:\n{url}\n"
                        "After authorizing, the flow will complete and the token will be saved."
                    )
                    creds = flow.run_local_server(port=0, authorization_prompt_message=auth_prompt, open_browser=True)

                # Persist the credentials for the next run
                try:
                    with open(token_path, "w") as token_f:
                        token_f.write(creds.to_json())
                        self._logger.info("Saved OAuth token to %s", token_path)
                except Exception:
                    self._logger.warning("Failed to write OAuth token to %s", token_path)

            return build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as error:
            raise DriveAuthError(f"Could not authenticate with Google Drive: {error}") from error

    def get_or_create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        project_key: str | None = None,
        created_map: dict | None = None,
    ) -> dict[str, str]:
        # Delegate to module-level helper that performs a name+parent search
        # and (optionally) uses an appProperties-based project_key to ensure
        # idempotent creations across calls.
        return get_or_create_folder(
            service=self._service,
            name=name,
            parent_id=parent_id,
            logger=self._logger,
            project_key=project_key,
            created_map=created_map,
            description=description,
        )



    def find_folder(self, name: str, parent_id: str | None = None) -> dict[str, str] | None:
        query_parts = [
            f"mimeType = '{FOLDER_MIME_TYPE}'",
            "trashed = false",
            f"name = '{self._escape_query_value(name)}'",
        ]
        if parent_id:
            query_parts.append(f"'{self._escape_query_value(parent_id)}' in parents")

        response = (
            self._service.files()
            .list(
                q=" and ".join(query_parts),
                spaces="drive",
                fields="files(id, name, webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=10,
            )
            .execute()
        )
        folders = response.get("files", [])
        if len(folders) > 1:
            self._logger.warning(
                "Found %s folders named %s under parent %s; using the first",
                len(folders),
                name,
                parent_id or "visible Drive scope",
            )
        return folders[0] if folders else None

    def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, str]:
        metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME_TYPE}
        if parent_id:
            metadata["parents"] = [parent_id]
        if description:
            metadata["description"] = description

        folder = (
            self._service.files()
            .create(
                body=metadata,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        self._logger.info("Created folder name=%s id=%s", name, folder["id"])
        return folder

    def ensure_folder_tree(
        self,
        parent_id: str,
        folder_tree: dict[str, dict],
        project_key: str | None = None,
    ) -> dict[str, int]:
        summary = {"created_count": 0, "existing_count": 0}
        # created_map stores in-memory created or resolved folders for this run
        created_map: dict[str, dict] = {}
        self._ensure_folder_tree_recursive(parent_id, folder_tree, summary, project_key=project_key, created_map=created_map)
        return summary

    def create_project_folder(
        self,
        project_name: str,
        customer_name: str,
        projects_folder_name: str = "Projects",
        description: str | None = None,
    ) -> dict[str, str]:
        """Find (or create) the top-level `projects_folder_name` folder and
        create a child folder named "{ProjectName}-{Customer name}" inside it.

        Returns the created or existing project folder resource.
        """
        # Normalize and compose the folder name
        safe_project = project_name.replace("/", "-").strip()
        safe_customer = customer_name.replace("/", "-").strip()
        folder_name = f"{safe_project}-{safe_customer}"

        # Find or create the parent "Projects" folder at root scope
        projects_folder = self.find_folder(projects_folder_name, parent_id=None)
        if not projects_folder:
            self._logger.info("Top-level folder '%s' not found; creating it", projects_folder_name)
            projects_folder = self.create_folder(projects_folder_name)

        # Create (or get) the project folder under Projects
        project_key = f"project:{safe_project}:{safe_customer}"
        project_folder = get_or_create_folder(
            service=self._service,
            name=folder_name,
            parent_id=projects_folder["id"],
            logger=self._logger,
            project_key=project_key,
            created_map=None,
            description=description,
        )
        return project_folder

    def _ensure_folder_tree_recursive(
        self,
        parent_id: str,
        folder_tree: dict[str, dict],
        summary: dict[str, int],
        project_key: str | None = None,
        created_map: dict | None = None,
    ) -> None:
        for folder_name, children in folder_tree.items():
            # For idempotency use a per-folder project key composed of the project_key base
            # and the specific folder name: ProjectKeyBase::FolderName
            per_folder_key = f"{project_key}::{folder_name}" if project_key else None
            # Prepare created_map
            if created_map is None:
                created_map = {}
            folder = get_or_create_folder(
                self._service,
                name=folder_name,
                parent_id=parent_id,
                logger=self._logger,
                project_key=per_folder_key,
                created_map=created_map,
            )
            # If the returned folder was already present, we consider it existing; otherwise created.
            # We infer this from whether the folder was found during the list call (module helper logged it).
            # To keep the accounting accurate without extra API calls, check if the folder name matched
            # an existing entry by attempting a light re-check of the list response was avoided; instead
            # increment existing_count when the folder creation did not happen (determined by logs is not
            # feasible), so use conservative approach: if folder was found via list, get_or_create_folder
            # returns that folder and we increment existing_count. To implement this, the helper returns a
            # dict with a marker `_was_created`.
            if folder.get("_was_created"):
                summary["created_count"] += 1
            else:
                summary["existing_count"] += 1

            if children:
                # propagate the same base project_key so deeper folders receive ProjectKeyBase::ChildName
                self._ensure_folder_tree_recursive(folder["id"], children, summary, project_key=project_key, created_map=created_map)

    @staticmethod
    def _escape_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")


def get_or_create_folder(
    service,
    name: str,
    parent_id: str | None = None,
    logger: logging.Logger | None = None,
    project_key: str | None = None,
    created_map: dict | None = None,
    description: str | None = None,
) -> dict:
    """Search for a folder by name + parent and create it if not found.

    Args:
        service: googleapiclient.discovery.Resource (Drive API service)
        name: Folder name to search/create
        parent_id: Parent folder ID to scope the search
        logger: Optional logger to emit messages

    Returns:
        dict: folder resource (contains at least 'id' and 'name'). Contains special key
              '_was_created' set to True when the folder was created by this call.
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    # Use the DriveService escaping helper defined above
    try:
        escaped_name = DriveService._escape_query_value(name)
    except Exception:
        # Fallback simple escape
        escaped_name = name.replace("'", "\\'")

    # Prepare created_map key for in-memory protection
    if created_map is None:
        created_map = {}
    map_key = f"{parent_id}::{name}"
    if map_key in created_map:
        logger.info("Folder exists in-memory, skipping name=%s", name)
        existing = created_map[map_key]
        existing["_was_created"] = False
        return existing

    # First, try to find by exact name + parent
    query = f"name='{escaped_name}' and mimeType='{FOLDER_MIME_TYPE}' and trashed=false"
    if parent_id:
        try:
            escaped_parent = DriveService._escape_query_value(parent_id)
        except Exception:
            escaped_parent = parent_id.replace("'", "\\'")
        query = f"name='{escaped_name}' and '{escaped_parent}' in parents and mimeType='{FOLDER_MIME_TYPE}' and trashed=false"

    response = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name, webViewLink, appProperties, description)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=1,
        )
        .execute()
    )

    folders = response.get("files", [])
    if folders:
        folder = folders[0]
        logger.info("Folder exists, skipping name=%s id=%s", name, folder.get("id"))
        folder["_was_created"] = False
        # cache in-memory
        created_map[map_key] = folder
        return folder

    # If not found by name, and project_key provided, try to find by appProperties
    if project_key:
        # Query for appProperties matching project_key under the parent
        # Drive query for appProperties: "appProperties has { key='project_key' and value='...'}"
        try:
            escaped_key_value = DriveService._escape_query_value(project_key)
        except Exception:
            escaped_key_value = project_key.replace("'", "\\'")

        appprop_query = f"appProperties has {{ key='project_key' and value='{escaped_key_value}' }} and mimeType='{FOLDER_MIME_TYPE}' and trashed=false"
        if parent_id:
            appprop_query = f"'{escaped_parent}' in parents and " + appprop_query

        resp2 = (
            service.files()
            .list(
                q=appprop_query,
                spaces="drive",
                fields="files(id, name, webViewLink, appProperties, description)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1,
            )
            .execute()
        )
        folders2 = resp2.get("files", [])
        if folders2:
            folder = folders2[0]
            logger.info("Folder exists, skipping (by project key) name=%s id=%s", name, folder.get("id"))
            folder["_was_created"] = False
            created_map[map_key] = folder
            return folder

    # Create folder since not found
    metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME_TYPE}
    if parent_id:
        metadata["parents"] = [parent_id]
    if description:
        metadata["description"] = description
    if project_key:
        metadata.setdefault("appProperties", {})["project_key"] = project_key

    folder = (
        service.files()
        .create(
            body=metadata,
            fields="id, name, webViewLink, appProperties",
            supportsAllDrives=True,
        )
        .execute()
    )
    logger.info("Folder created name=%s id=%s", name, folder.get("id"))
    folder["_was_created"] = True
    created_map[map_key] = folder
    return folder
