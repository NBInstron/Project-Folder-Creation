import logging
import mimetypes
import json
import os
import socket
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveAuthError(RuntimeError):
    pass


class DriveService:

    def __init__(self, config: Any):
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._service = self._build_service()

    def _load_credentials(self) -> Credentials:
        token_json = (
            os.getenv("GOOGLE_OAUTH_TOKEN_JSON")
            or os.getenv("OAUTH_TOKEN_JSON")
        )

        if token_json:
            try:
                token_info = json.loads(token_json)
            except json.JSONDecodeError as error:
                raise DriveAuthError(
                    "Google OAuth token JSON environment variable is not valid JSON"
                ) from error

            return Credentials.from_authorized_user_info(
                token_info,
                scopes=DRIVE_SCOPES
            )

        token_file = Path(self._config.oauth_token_file or "oauth_token.json")

        if not token_file.is_file():
            raise DriveAuthError(
                "Google OAuth token not found. Set GOOGLE_OAUTH_TOKEN_JSON "
                f"or provide OAUTH_TOKEN_FILE at {token_file}."
            )

        return Credentials.from_authorized_user_file(
            str(token_file),
            scopes=DRIVE_SCOPES
        )

    def _refresh_credentials(self, credentials: Credentials) -> Credentials:
        if credentials.valid:
            return credentials

        if credentials.refresh_token:
            credentials.refresh(Request())
            self._save_refreshed_credentials(credentials)
            return credentials

        raise DriveAuthError(
            "Google OAuth token is invalid or missing a refresh token. "
            "Generate a new OAuth token with the Drive scope."
        )

    def _save_refreshed_credentials(self, credentials: Credentials) -> None:
        if os.getenv("GOOGLE_OAUTH_TOKEN_JSON") or os.getenv("OAUTH_TOKEN_JSON"):
            return

        token_file = Path(self._config.oauth_token_file or "oauth_token.json")

        try:
            token_file.write_text(credentials.to_json(), encoding="utf-8")
        except OSError:
            self._logger.warning(
                "Could not persist refreshed Google OAuth token to %s",
                token_file,
            )

    def _build_service(self):

        try:

            credentials = self._refresh_credentials(self._load_credentials())

            service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )

            self._logger.info("Google Drive client initialized successfully")

            return service

        except Exception as error:

            self._logger.exception("Google Drive authentication failed")

            raise DriveAuthError(
                f"Could not authenticate with Google Drive: {error}"
            ) from error

    @staticmethod
    def _escape_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def find_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> dict | None:

        escaped_name = self._escape_query_value(name)

        query = (
            f"name='{escaped_name}' "
            f"and mimeType='{FOLDER_MIME_TYPE}' "
            f"and trashed=false"
        )

        if parent_id:
            query += f" and '{parent_id}' in parents"

        response = (
            self._service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id,name,webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1,
            )
            .execute(num_retries=3)
        )

        folders = response.get("files", [])
        return folders[0] if folders else None

    def get_or_create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
    ) -> dict:

        try:

            escaped_name = self._escape_query_value(name)

            query = (
                f"name='{escaped_name}' "
                f"and mimeType='{FOLDER_MIME_TYPE}' "
                f"and trashed=false"
            )

            if parent_id:
                query += f" and '{parent_id}' in parents"

            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="files(id,name,webViewLink)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    pageSize=1,
                )
                .execute(num_retries=3)
            )

            folders = response.get("files", [])

            if folders:

                folder = folders[0]
                folder["_was_created"] = False

                self._logger.info(
                    "Folder exists: %s",
                    folder["name"]
                )

                return folder

            metadata = {
                "name": name,
                "mimeType": FOLDER_MIME_TYPE,
            }

            if parent_id:
                metadata["parents"] = [parent_id]

            if description:
                metadata["description"] = description

            folder = (
                self._service.files()
                .create(
                    body=metadata,
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute(num_retries=3)
            )

            folder["_was_created"] = True

            self._logger.info(
                "Folder created: %s",
                folder["name"]
            )

            return folder

        except socket.timeout:
            self._logger.exception("Google Drive timeout")
            raise Exception("Google Drive timeout")

        except HttpError as error:
            self._logger.exception("Google Drive API error")
            raise Exception(str(error))

        except Exception as error:
            self._logger.exception("Folder creation failed")
            raise Exception(str(error))

    def ensure_folder_tree(
        self,
        parent_id: str,
        folder_tree: dict,
    ):

        summary = {
            "created_count": 0,
            "existing_count": 0,
            "errors": 0
        }

        self._ensure_folder_tree_recursive(
            parent_id,
            folder_tree,
            summary,
            level=0
        )

        return summary

    def _ensure_folder_tree_recursive(
        self,
        parent_id: str,
        folder_tree: dict,
        summary: dict,
        level: int = 0
    ):

        # Prevent infinite recursion
        if level > 20:
            self._logger.warning("Max recursion depth reached")
            return

        for folder_name, children in folder_tree.items():

            try:

                self._logger.info(
                    "Processing folder: %s",
                    folder_name
                )

                folder = self.get_or_create_folder(
                    name=folder_name,
                    parent_id=parent_id,
                )

                if folder.get("_was_created"):
                    summary["created_count"] += 1
                else:
                    summary["existing_count"] += 1

                if isinstance(children, dict) and children:

                    self._ensure_folder_tree_recursive(
                        parent_id=folder["id"],
                        folder_tree=children,
                        summary=summary,
                        level=level + 1
                    )

            except Exception as error:

                self._logger.exception(
                    "Failed folder: %s",
                    folder_name
                )

                summary["errors"] += 1

                continue

    def create_project_folder(
        self,
        project_name: str,
        customer_name: str,
        projects_folder_name: str,
        description: str | None = None,
    ) -> dict:

        projects_folder = self.get_or_create_folder(
            name=projects_folder_name,
            parent_id=self._config.demo_projects_parent_id,
        )

        project_folder = self.get_or_create_folder(
            name=project_name,
            parent_id=projects_folder["id"],
            description=description or f"Customer: {customer_name}",
        )

        if project_folder.get("_was_created"):
            self.ensure_folder_tree(
                parent_id=project_folder["id"],
                folder_tree=self._get_project_folder_tree(),
            )
            self.ensure_project_scope_templates(project_folder["id"])

        return project_folder

    @staticmethod
    def _get_project_folder_tree() -> dict:
        from services.folder_structure import PROJECT_FOLDER_TREE

        return PROJECT_FOLDER_TREE

    def upload_file_if_not_exists(
        self,
        local_path: Path,
        parent_id: str,
        target_name: str | None = None,
    ):

        try:

            if not local_path.exists():
                return None

            name = target_name or local_path.name

            escaped_name = self._escape_query_value(name)

            query = (
                f"name='{escaped_name}' "
                f"and '{parent_id}' in parents "
                f"and trashed=false"
            )

            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="files(id,name)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    pageSize=1,
                )
                .execute(num_retries=3)
            )

            files = response.get("files", [])

            if files:

                file_data = files[0]
                file_data["_was_uploaded"] = False

                return file_data

            mime_type, _ = mimetypes.guess_type(str(local_path))

            media = MediaFileUpload(
                str(local_path),
                mimetype=mime_type or "application/octet-stream",
                resumable=False
            )

            metadata = {
                "name": name,
                "parents": [parent_id]
            }

            uploaded = (
                self._service.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id,name",
                    supportsAllDrives=True,
                )
                .execute(num_retries=3)
            )

            uploaded["_was_uploaded"] = True

            return uploaded

        except Exception as error:

            self._logger.exception(
                "File upload failed"
            )

            return None

    def ensure_project_scope_templates(
        self,
        project_folder_id: str,
        template_dir: Path | None = None,
    ) -> dict:

        template_dir = template_dir or Path(__file__).resolve().parents[1] / "Templates"

        summary = {
            "files_uploaded": 0,
            "files_skipped": 0,
            "errors": 0,
        }

        if not template_dir.exists():
            self._logger.warning("Template directory not found: %s", template_dir)
            summary["errors"] += 1
            return summary

        project_folder = (
            self._service.files()
            .get(
                fileId=project_folder_id,
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute(num_retries=3)
        )
        project_name = project_folder.get("name", "ProjectName")

        for local_path in sorted(path for path in template_dir.iterdir() if path.is_file()):
            target_name = local_path.name.replace("ProjectName", project_name)

            uploaded = self.upload_file_if_not_exists(
                local_path=local_path,
                parent_id=project_folder_id,
                target_name=target_name,
            )

            if not uploaded:
                summary["errors"] += 1
                continue

            if uploaded.get("_was_uploaded"):
                summary["files_uploaded"] += 1
            else:
                summary["files_skipped"] += 1

        return summary


def get_or_create_folder(
    service: Any,
    name: str,
    parent_id: str | None = None,
    logger: logging.Logger | None = None,
    created_map: dict | None = None,
) -> dict:

    logger = logger or logging.getLogger(__name__)
    created_map = created_map if created_map is not None else {}
    cache_key = (parent_id, name)

    if cache_key in created_map:
        return created_map[cache_key]

    escaped_name = DriveService._escape_query_value(name)

    query = (
        f"name='{escaped_name}' "
        f"and mimeType='{FOLDER_MIME_TYPE}' "
        f"and trashed=false"
    )

    if parent_id:
        query += f" and '{parent_id}' in parents"

    response = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id,name,webViewLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=1,
        )
        .execute(num_retries=3)
    )

    folders = response.get("files", [])

    if folders:
        logger.info("Folder exists: %s", folders[0].get("name"))
        return folders[0]

    metadata = {
        "name": name,
        "mimeType": FOLDER_MIME_TYPE,
    }

    if parent_id:
        metadata["parents"] = [parent_id]

    folder = (
        service.files()
        .create(
            body=metadata,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute(num_retries=3)
    )

    created_map[cache_key] = folder
    logger.info("Folder created: %s", folder.get("name"))

    return folder
