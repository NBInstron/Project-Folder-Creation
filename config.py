import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    credentials_file: Path
    demo_projects_folder_name: str
    demo_projects_parent_id: str | None
    webhook_token: str | None
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
            credentials_file=Path(credentials),
            demo_projects_folder_name=os.getenv("DEMO_PROJECTS_FOLDER_NAME", "Projects"),
            demo_projects_parent_id=demo_projects_parent_id,
            webhook_token=os.getenv("WEBHOOK_TOKEN") or None,
            log_file=Path(os.getenv("LOG_FILE", "logs/app.log")),
            flask_host=os.getenv("FLASK_HOST", "0.0.0.0"),
            flask_port=int(os.getenv("FLASK_PORT", "5000")),
            flask_debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
        )
