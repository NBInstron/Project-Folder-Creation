import logging
import mimetypes
import socket
from pathlib import Path
from typing import Any

import httplib2
from google.oauth2 import service_account
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
        self._service = self._build_service(config.credentials_file)

    def _build_service(self, credentials_file: Path):

        try:

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file),
                scopes=DRIVE_SCOPES
            )

            http = httplib2.Http(timeout=120)

            service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
                http=http
            )

            self._logger.info("Google Drive authentication successful")

            return service

        except Exception as error:

            self._logger.exception("Google Drive authentication failed")

            raise DriveAuthError(
                f"Could not authenticate with Google Drive: {error}"
            ) from error

    @staticmethod
    def _escape_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

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
            "existing_count": 0
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

                continue

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