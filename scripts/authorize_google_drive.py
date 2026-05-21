from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import Config
from services.drive_service import DriveService


def main() -> None:
    config = Config.from_env()
    DriveService(config)
    print(f"Google Drive service account authentication is ready: {config.credentials_file}")


if __name__ == "__main__":
    main()
