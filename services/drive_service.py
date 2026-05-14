import logging
import mimetypes
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive"
]


class DriveAuthError(RuntimeError):
    pass


class DriveService:

    def __init__(self, config: Any):

        self._config = config
        self._logger = logging.getLogger(__name__)

        self._service = self._build_service(
            config.credentials_file
        )

    # =========================================================
    # AUTHENTICATION
    # =========================================================

    def _build_service(self, credentials_file: Path):

        try:

            self._logger.info(
                "Using credentials file: %s",
                credentials_file
            )

            credentials = (
                service_account.Credentials
                .from_service_account_file(
                    str(credentials_file),
                    scopes=DRIVE_SCOPES
                )
            )

            service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False
            )

            self._logger.info(
                "Google Drive authentication successful"
            )

            return service

        except Exception as error:

            self._logger.exception(
                "Google Drive authentication failed"
            )

            raise DriveAuthError(
                f"Could not authenticate with Google Drive: {error}"
            ) from error

    # =========================================================
    # CREATE PROJECT FOLDER
    # =========================================================

    def create_project_folder(
        self,
        project_name: str,
        customer_name: str,
        projects_folder_name: str = "Projects",
        description: str | None = None,
    ) -> dict[str, str]:

        safe_project = (
            project_name
            .replace("/", "-")
            .replace("\\", "-")
            .strip()
        )

        safe_customer = (
            customer_name
            .replace("/", "-")
            .replace("\\", "-")
            .strip()
        )

        folder_name = f"{safe_project}-{safe_customer}"

        projects_folder = self.find_folder(
            projects_folder_name
        )

        if not projects_folder:

            self._logger.info(
                "Projects folder not found. Creating..."
            )

            projects_folder = self.create_folder(
                projects_folder_name
            )

        project_key = (
            f"project:{safe_project}:{safe_customer}"
        )

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

    # =========================================================
    # FIND FOLDER
    # =========================================================

    def find_folder(
        self,
        name: str,
        parent_id: str | None = None
    ):

        query_parts = [
            f"mimeType='{FOLDER_MIME_TYPE}'",
            "trashed=false",
            f"name='{self._escape_query_value(name)}'"
        ]

        if parent_id:

            query_parts.append(
                f"'{self._escape_query_value(parent_id)}' in parents"
            )

        response = (
            self._service.files()
            .list(
                q=" and ".join(query_parts),
                spaces="drive",
                fields="files(id,name,webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1,
            )
            .execute()
        )

        folders = response.get("files", [])

        return folders[0] if folders else None

    # =========================================================
    # CREATE FOLDER
    # =========================================================

    def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
    ):

        metadata: dict[str, Any] = {
            "name": name,
            "mimeType": FOLDER_MIME_TYPE
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
            .execute()
        )

        self._logger.info(
            "Created folder: %s",
            name
        )

        return folder

    def get_or_create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
        project_key: str | None = None,
        created_map: dict | None = None,
    ):

        return get_or_create_folder(
            service=self._service,
            name=name,
            parent_id=parent_id,
            logger=self._logger,
            project_key=project_key,
            created_map=created_map,
            description=description,
        )
    # =========================================================
    # ENSURE FOLDER TREE
    # =========================================================

    def ensure_folder_tree(
        self,
        parent_id: str,
        folder_tree: dict[str, dict],
        project_key: str | None = None,
    ):

        summary = {
            "created_count": 0,
            "existing_count": 0,
            "errors": 0
        }

        created_map = {}

        self._ensure_folder_tree_recursive(
            parent_id=parent_id,
            folder_tree=folder_tree,
            summary=summary,
            project_key=project_key,
            created_map=created_map
        )

        return summary

    # =========================================================
    # RECURSIVE FOLDER CREATION
    # =========================================================

    def _ensure_folder_tree_recursive(
        self,
        parent_id: str,
        folder_tree: dict[str, dict],
        summary: dict[str, int],
        project_key: str | None = None,
        created_map: dict | None = None,
    ) -> None:

        if created_map is None:
            created_map = {}

        for folder_name, children in folder_tree.items():

            try:

                folder_name = (
                    folder_name
                    .strip()
                    .replace("/", "-")
                    .replace("\\", "-")
                    .replace(":", "-")
                )

                self._logger.info(
                    "Processing folder: %s",
                    folder_name
                )

                per_folder_key = (
                    f"{project_key}::{folder_name}"
                    if project_key
                    else None
                )

                folder = get_or_create_folder(
                    service=self._service,
                    name=folder_name,
                    parent_id=parent_id,
                    logger=self._logger,
                    project_key=per_folder_key,
                    created_map=created_map,
                )

                if folder.get("_was_created"):

                    summary["created_count"] += 1

                else:

                    summary["existing_count"] += 1

                # SAFE RECURSION
                if isinstance(children, dict) and children:

                    self._ensure_folder_tree_recursive(
                        parent_id=folder["id"],
                        folder_tree=children,
                        summary=summary,
                        project_key=project_key,
                        created_map=created_map
                    )

            except Exception as error:

                summary["errors"] += 1

                self._logger.exception(
                    "Failed while processing folder: %s",
                    folder_name
                )

                continue

    # =========================================================
    # FILE UPLOAD
    # =========================================================

    def upload_file_if_not_exists(
        self,
        local_path: Path,
        parent_id: str,
        target_name: str | None = None,
    ):

        if not local_path.exists():

            self._logger.warning(
                "File not found: %s",
                local_path
            )

            return None

        name = target_name or local_path.name

        escaped_parent = self._escape_query_value(
            parent_id
        )

        escaped_name = self._escape_query_value(
            name
        )

        query = (
            f"name='{escaped_name}' "
            f"and '{escaped_parent}' in parents "
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
            .execute()
        )

        files = response.get("files", [])

        if files:

            existing = files[0]

            existing["_was_uploaded"] = False

            self._logger.info(
                "File already exists: %s",
                name
            )

            return existing

        mime_type, _ = mimetypes.guess_type(
            str(local_path)
        )

        media = MediaFileUpload(
            str(local_path),
            mimetype=(
                mime_type
                or "application/octet-stream"
            ),
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
            .execute()
        )

        uploaded["_was_uploaded"] = True

        self._logger.info(
            "Uploaded file: %s",
            name
        )

        return uploaded

    # =========================================================
    # ESCAPE QUERY
    # =========================================================

    @staticmethod
    def _escape_query_value(value: str):

        return (
            value
            .replace("\\", "\\\\")
            .replace("'", "\\'")
        )


# =============================================================
# GET OR CREATE FOLDER
# =============================================================

def get_or_create_folder(
    service,
    name: str,
    parent_id: str | None = None,
    logger: logging.Logger | None = None,
    project_key: str | None = None,
    created_map: dict | None = None,
    description: str | None = None,
):

    if logger is None:
        logger = logging.getLogger(__name__)

    if created_map is None:
        created_map = {}

    map_key = f"{parent_id}::{name}"

    if map_key in created_map:

        existing = created_map[map_key]

        existing["_was_created"] = False

        return existing

    escaped_name = (
        name
        .replace("\\", "\\\\")
        .replace("'", "\\'")
    )

    query = (
        f"name='{escaped_name}' "
        f"and mimeType='{FOLDER_MIME_TYPE}' "
        f"and trashed=false"
    )

    if parent_id:

        escaped_parent = (
            parent_id
            .replace("\\", "\\\\")
            .replace("'", "\\'")
        )

        query = (
            f"name='{escaped_name}' "
            f"and '{escaped_parent}' in parents "
            f"and mimeType='{FOLDER_MIME_TYPE}' "
            f"and trashed=false"
        )

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
        .execute()
    )

    folders = response.get("files", [])

    if folders:

        folder = folders[0]

        folder["_was_created"] = False

        created_map[map_key] = folder

        logger.info(
            "Folder already exists: %s",
            name
        )

        return folder

    metadata = {
        "name": name,
        "mimeType": FOLDER_MIME_TYPE
    }

    if parent_id:
        metadata["parents"] = [parent_id]

    if description:
        metadata["description"] = description

    if project_key:

        metadata.setdefault(
            "appProperties",
            {}
        )["project_key"] = project_key

    folder = (
        service.files()
        .create(
            body=metadata,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    folder["_was_created"] = True

    created_map[map_key] = folder

    logger.info(
        "Folder created: %s",
        name
    )

    return folder