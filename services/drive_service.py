import logging
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_TEMPLATE_ROOT_FOLDER_NAME = "ITA-XXX_AI_Enebelment"
DEFAULT_TEMPLATE_FOLDER_NAME = "Template"
PROJECT_SCOPE_TEMPLATES = (
    (
        (
            "<ProjectName>_CLT_03_R01.xlsm",
            "<ProjectName>_CLT_03_R01.xlsx",
            "01. <ProjectName>_CLT_03_R01.xlsm",
            "01. <ProjectName>_CLT_03_R01.xlsx",
            "01.<ProjectName>_CLT_03_R01.xlsm",
            "01.<ProjectName>_CLT_03_R01.xlsx",
        ),
        "01.{project_name}_CLT_03_R01.xlsm",
        ("clt_03_r01",),
        (".xlsm", ".xlsx"),
    ),
    (
        (
            "<ProjectName>_SOW_R01.docx",
            "<ProjectName>_SOW_R01.docx",
            "02. <ProjectName>_SOW_R01.docx",
            "02. <ProjectName>_SOW_R01.docx",
            "02.<ProjectName>_SOW_R01.docx",
            "02.<ProjectName>_SOW_R01.docx",
        ),
        "02.{project_name}_SOW_R01.docx",
        ("sow",),
        (".docx",),
    ),
)


class DriveAuthError(RuntimeError):
    pass


class DriveService:
    def __init__(self, config: Any):
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._shared_drive_id = getattr(config, "google_shared_drive_id", None)
        self._projects_folder_id = getattr(config, "drive_projects_folder_id", None)
        self._template_folder_id = getattr(config, "drive_template_folder_id", None)
        self._service = self._build_service(config)
        self._log_shared_drive_configuration()

    def _build_service(self, config: Any):
        auth_mode = getattr(config, "google_auth_mode", "service_account").lower()
        if auth_mode != "service_account":
            raise DriveAuthError(
                f"Unsupported GOOGLE_AUTH_MODE={auth_mode!r}. "
                "This application uses service account authentication only."
            )

        credentials = self._build_service_account_credentials(config)
        try:
            return build("drive", "v3", credentials=credentials, cache_discovery=False)
        except Exception as error:
            raise DriveAuthError(f"Could not authenticate with Google Drive: {error}") from error

    def _build_service_account_credentials(self, config: Any):
        credentials_file = config.credentials_file
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
            impersonated_user = getattr(config, "google_impersonated_user", None)
            if impersonated_user:
                credentials = credentials.with_subject(impersonated_user)
                self._logger.info("Using delegated Google Drive user=%s", impersonated_user)
            return credentials
        except Exception as error:
            raise DriveAuthError(f"Could not authenticate with Google Drive: {error}") from error

    def _drive_list_kwargs(self, q: str, fields: str, page_size: int = 10) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "q": q,
            "spaces": "drive",
            "fields": fields,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": page_size,
        }
        shared_drive_id = getattr(self, "_shared_drive_id", None)
        if shared_drive_id:
            kwargs["corpora"] = "drive"
            kwargs["driveId"] = shared_drive_id
        return kwargs

    def _log_shared_drive_configuration(self) -> None:
        self._logger.info(
            "Drive validation auth_mode=service_account shared_drive_id=%s projects_folder_id=%s template_folder_id=%s service_account=%s",
            self._shared_drive_id or "not-configured",
            self._projects_folder_id or "not-configured",
            self._template_folder_id or "not-configured",
            "projectserp@projects-495410.iam.gserviceaccount.com",
        )
        if not self._shared_drive_id:
            self._logger.warning("Shared Drive ID is not configured; Drive searches may include My Drive.")
            return

        try:
            drive = (
                self._service.drives()
                .get(driveId=self._shared_drive_id, fields="id, name")
                .execute()
            )
            self._logger.info("Shared Drive validation succeeded id=%s name=%s", drive.get("id"), drive.get("name"))
        except Exception:
            self._logger.exception("Shared Drive validation failed id=%s", self._shared_drive_id)
            raise

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
            drive_id=self._shared_drive_id,
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
            .list(**self._drive_list_kwargs(
                q=" and ".join(query_parts),
                fields="files(id, name, webViewLink, driveId, parents)",
                page_size=10,
            ))
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

    def get_folder_by_id(self, folder_id: str) -> dict[str, str]:
        folder = (
            self._service.files()
            .get(
                fileId=folder_id,
                fields="id, name, webViewLink, mimeType, driveId, parents",
                supportsAllDrives=True,
            )
            .execute()
        )
        if folder.get("mimeType") != FOLDER_MIME_TYPE:
            raise FileNotFoundError(f"Google Drive item is not a folder: {folder_id}")
        return folder

    def find_file(self, name: str, parent_id: str) -> dict[str, str] | None:
        query_parts = [
            f"name = '{self._escape_query_value(name)}'",
            f"'{self._escape_query_value(parent_id)}' in parents",
            f"mimeType != '{FOLDER_MIME_TYPE}'",
            "trashed = false",
        ]

        response = (
            self._service.files()
            .list(**self._drive_list_kwargs(
                q=" and ".join(query_parts),
                fields="files(id, name, webViewLink, mimeType, driveId, parents)",
                page_size=10,
            ))
            .execute()
        )
        files = response.get("files", [])
        if len(files) > 1:
            self._logger.warning(
                "Found %s files named %s under parent %s; using the first",
                len(files),
                name,
                parent_id,
        )
        return files[0] if files else None

    def list_files(self, parent_id: str) -> list[dict[str, str]]:
        response = (
            self._service.files()
            .list(**self._drive_list_kwargs(
                q=(
                    f"'{self._escape_query_value(parent_id)}' in parents and "
                    f"mimeType != '{FOLDER_MIME_TYPE}' and trashed = false"
                ),
                fields="files(id, name, webViewLink, mimeType, driveId, parents)",
                page_size=100,
            ))
            .execute()
        )
        return response.get("files", [])

    def copy_file_if_missing(
        self,
        source_file_id: str,
        drive_name: str,
        parent_id: str,
    ) -> dict[str, Any]:
        existing = self.find_file(drive_name, parent_id)
        if existing:
            self._logger.info(
                "File already exists name=%s id=%s parent_id=%s",
                drive_name,
                existing.get("id"),
                parent_id,
            )
            return {**existing, "_copy_status": "existing"}

        try:
            copied = (
                self._service.files()
                .copy(
                    fileId=source_file_id,
                    body={"name": drive_name, "parents": [parent_id]},
                    fields="id, name, webViewLink, mimeType",
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError:
            self._logger.exception(
                "Drive copy failed source_file_id=%s target_name=%s parent_id=%s",
                source_file_id,
                drive_name,
                parent_id,
            )
            raise
        except Exception:
            self._logger.exception(
                "Drive copy failed source_file_id=%s target_name=%s parent_id=%s",
                source_file_id,
                drive_name,
                parent_id,
            )
            raise

        self._logger.info(
            "File copied name=%s id=%s parent_id=%s source_file_id=%s",
            drive_name,
            copied.get("id"),
            parent_id,
            source_file_id,
        )
        copied["_copy_status"] = "copied"
        return copied

    def upload_file_if_missing(
        self,
        local_path: str,
        drive_name: str,
        parent_id: str,
        mimetype: str | None = None,
    ) -> dict[str, Any]:
        existing = self.find_file(drive_name, parent_id)
        if existing:
            self._logger.info(
                "Upload skipped; file already exists name=%s id=%s parent_id=%s",
                drive_name,
                existing.get("id"),
                parent_id,
            )
            return {**existing, "_upload_status": "existing"}

        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=True)
        metadata = {"name": drive_name, "parents": [parent_id]}
        uploaded = (
            self._service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, name, webViewLink, mimeType",
                supportsAllDrives=True,
            )
            .execute()
        )
        self._logger.info("File uploaded name=%s id=%s parent_id=%s", drive_name, uploaded.get("id"), parent_id)
        uploaded["_upload_status"] = "uploaded"
        return uploaded

    def copy_template_file_if_missing(
        self,
        source_template_name: str | tuple[str, ...],
        drive_name: str,
        source_folder_id: str,
        parent_id: str,
        required_terms: tuple[str, ...] = (),
        allowed_extensions: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        existing = self.find_file(drive_name, parent_id)
        if existing:
            self._logger.info(
                "Template target file already exists name=%s id=%s parent_id=%s",
                drive_name,
                existing.get("id"),
                parent_id,
            )
            return {**existing, "_copy_status": "existing"}

        source_template_names = (
            (source_template_name,)
            if isinstance(source_template_name, str)
            else source_template_name
        )
        source_file = None
        for candidate_name in source_template_names:
            source_file = self.find_file(candidate_name, source_folder_id)
            if source_file:
                self._logger.info(
                    "Template source found name=%s id=%s source_folder_id=%s",
                    candidate_name,
                    source_file.get("id"),
                    source_folder_id,
                )
                break

        if not source_file and (required_terms or allowed_extensions):
            matching_files = []
            for file_resource in self.list_files(source_folder_id):
                file_name = file_resource.get("name", "")
                normalized_name = file_name.lower()
                has_terms = all(term.lower() in normalized_name for term in required_terms)
                has_extension = (
                    not allowed_extensions
                    or normalized_name.endswith(tuple(ext.lower() for ext in allowed_extensions))
                )
                if has_terms and has_extension:
                    matching_files.append(file_resource)

            if len(matching_files) > 1:
                self._logger.warning(
                    "Found %s possible template files for target=%s; using name=%s id=%s",
                    len(matching_files),
                    drive_name,
                    matching_files[0].get("name"),
                    matching_files[0].get("id"),
                )
            if matching_files:
                source_file = matching_files[0]
                self._logger.info(
                    "Template source found by fallback name=%s id=%s source_folder_id=%s",
                    source_file.get("name"),
                    source_file.get("id"),
                    source_folder_id,
                )

        if not source_file:
            raise FileNotFoundError(
                "Template file not found in Google Drive Template folder. "
                f"Tried: {', '.join(source_template_names)}"
            )

        return self.copy_file_if_missing(source_file["id"], drive_name, parent_id)

    def resolve_template_folder(
        self,
        template_root_folder_name: str = DEFAULT_TEMPLATE_ROOT_FOLDER_NAME,
        template_folder_name: str = DEFAULT_TEMPLATE_FOLDER_NAME,
        template_root_folder_id: str | None = None,
        template_folder_id: str | None = None,
    ) -> dict[str, str]:
        if template_folder_id:
            folder = self.get_folder_by_id(template_folder_id)
            self._logger.info("Using Google Drive template folder id=%s name=%s", folder["id"], folder["name"])
            return folder

        if template_root_folder_id:
            root_folder = self.get_folder_by_id(template_root_folder_id)
        else:
            root_folder = self.find_folder(template_root_folder_name)
            if not root_folder:
                raise FileNotFoundError(
                    f"Google Drive template root folder not found: {template_root_folder_name}"
                )

        template_folder = self.find_folder(template_folder_name, parent_id=root_folder["id"])
        if not template_folder:
            raise FileNotFoundError(
                f"Google Drive template folder not found: {root_folder['name']}/{template_folder_name}"
            )

        self._logger.info(
            "Resolved Google Drive template folder root=%s template=%s",
            root_folder.get("id"),
            template_folder.get("id"),
        )
        return template_folder

    def copy_project_scope_files(
        self,
        project_folder_id: str,
        project_name: str,
        template_root_folder_name: str = DEFAULT_TEMPLATE_ROOT_FOLDER_NAME,
        template_folder_name: str = DEFAULT_TEMPLATE_FOLDER_NAME,
        template_root_folder_id: str | None = None,
        template_folder_id: str | None = None,
    ) -> dict[str, Any]:
        """Copy project-scope templates from Google Drive into the project tree."""
        logger = getattr(self, "_logger", logging.getLogger(__name__))
        logger.info(
            "Starting project scope template copy project=%s project_folder_id=%s",
            project_name,
            project_folder_id,
        )
        project_folder = self.get_or_create_folder("05. Project", parent_id=project_folder_id)
        project_scope_folder = self.get_or_create_folder(
            "03. Project Scope",
            parent_id=project_folder["id"],
        )
        template_folder = self.resolve_template_folder(
            template_root_folder_name=template_root_folder_name,
            template_folder_name=template_folder_name,
            template_root_folder_id=template_root_folder_id,
            template_folder_id=template_folder_id,
        )

        copied_files = []
        summary = {
            "folder": project_scope_folder,
            "copied_count": 0,
            "existing_count": 0,
            "files": copied_files,
        }
        for source_template_names, target_template, required_terms, allowed_extensions in PROJECT_SCOPE_TEMPLATES:
            drive_name = target_template.format(project_name=project_name)
            file_resource = self.copy_template_file_if_missing(
                source_template_name=source_template_names,
                drive_name=drive_name,
                source_folder_id=template_folder["id"],
                parent_id=project_scope_folder["id"],
                required_terms=required_terms,
                allowed_extensions=allowed_extensions,
            )
            copied_files.append(file_resource)
            if file_resource.get("_copy_status") == "existing":
                summary["existing_count"] += 1
            else:
                summary["copied_count"] += 1

        return summary

    def create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, str]:
        metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME_TYPE}
        if parent_id:
            metadata["parents"] = [parent_id]
        elif getattr(self, "_shared_drive_id", None):
            metadata["parents"] = [self._shared_drive_id]
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

        projects_folder_id = getattr(self, "_projects_folder_id", None)
        if projects_folder_id:
            projects_folder = self.get_folder_by_id(projects_folder_id)
            self._logger.info(
                "Using configured Projects folder id=%s name=%s",
                projects_folder.get("id"),
                projects_folder.get("name"),
            )
        else:
            projects_folder = self.find_folder(projects_folder_name, parent_id=None)
        if not projects_folder:
            self._logger.info("Projects folder '%s' not found in Shared Drive; creating it", projects_folder_name)
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
            drive_id=self._shared_drive_id,
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
                drive_id=self._shared_drive_id,
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
    drive_id: str | None = None,
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

    list_kwargs = {
        "q": query,
        "spaces": "drive",
        "fields": "files(id, name, webViewLink, appProperties, description)",
        "includeItemsFromAllDrives": True,
        "supportsAllDrives": True,
        "pageSize": 1,
    }
    if drive_id:
        list_kwargs["corpora"] = "drive"
        list_kwargs["driveId"] = drive_id

    response = service.files().list(**list_kwargs).execute()

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

        appprop_list_kwargs = {
            **list_kwargs,
            "q": appprop_query,
        }
        resp2 = service.files().list(**appprop_list_kwargs).execute()
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
    elif drive_id:
        metadata["parents"] = [drive_id]
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
