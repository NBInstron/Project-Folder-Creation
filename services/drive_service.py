import logging
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build


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
        if not credentials_file.exists():
            raise DriveAuthError(
                f"Credentials file not found: {credentials_file}. "
                "Set GOOGLE_APPLICATION_CREDENTIALS or place credentials.json in the project root."
            )

        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file),
                scopes=DRIVE_SCOPES,
            )
            return build("drive", "v3", credentials=credentials, cache_discovery=False)
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
