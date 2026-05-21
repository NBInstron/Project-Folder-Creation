import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    google_auth_mode: str
    credentials_file: Path
    google_impersonated_user: str | None
    google_shared_drive_id: str | None
    drive_projects_folder_id: str | None
    drive_template_root_folder_name: str
    drive_template_root_folder_id: str | None
    drive_template_folder_name: str
    drive_template_folder_id: str | None
    demo_projects_folder_name: str
    demo_projects_parent_id: str | None
    webhook_token: str | None
    webhook_async: bool
    folder_job_workers: int
    processed_dir: Path
    log_file: Path
    flask_host: str
    flask_port: int
    flask_debug: bool

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        demo_projects_parent_id = os.getenv("DEMO_PROJECTS_PARENT_ID") or None

        return cls(
            google_auth_mode=os.getenv("GOOGLE_AUTH_MODE", "service_account").lower(),
            credentials_file=Path(credentials),
            google_impersonated_user=os.getenv("GOOGLE_IMPERSONATED_USER") or None,
            google_shared_drive_id=os.getenv("GOOGLE_SHARED_DRIVE_ID", "0AKpxt6N6-Ua8Uk9PVA") or None,
            drive_projects_folder_id=os.getenv(
                "DRIVE_PROJECTS_FOLDER_ID",
                "1j632OZrHEb6Ixcs8t8b5c_cluf2QaUZ0",
            ) or None,
            drive_template_root_folder_name=os.getenv(
                "DRIVE_TEMPLATE_ROOT_FOLDER_NAME",
                "ITA-XXX_AI_Enebelment",
            ),
            drive_template_root_folder_id=os.getenv("DRIVE_TEMPLATE_ROOT_FOLDER_ID") or None,
            drive_template_folder_name=os.getenv("DRIVE_TEMPLATE_FOLDER_NAME", "Template"),
            drive_template_folder_id=os.getenv(
                "DRIVE_TEMPLATE_FOLDER_ID",
                "1W7TE6nozGxW7fDkxKozXoRkGJDeQBaEt",
            ) or None,
            demo_projects_folder_name=os.getenv("DEMO_PROJECTS_FOLDER_NAME", "Projects"),
            demo_projects_parent_id=demo_projects_parent_id,
            webhook_token=os.getenv("WEBHOOK_TOKEN") or None,
            webhook_async=os.getenv("WEBHOOK_ASYNC", "true").lower() == "true",
            folder_job_workers=int(os.getenv("FOLDER_JOB_WORKERS", "2")),
            processed_dir=Path(os.getenv("PROCESSED_DIR", "processed")),
            log_file=Path(os.getenv("LOG_FILE", "logs/app.log")),
            flask_host=os.getenv("FLASK_HOST", "0.0.0.0"),
            flask_port=int(os.getenv("FLASK_PORT", "5000")),
            flask_debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        )
