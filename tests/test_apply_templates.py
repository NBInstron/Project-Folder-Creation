import os
from pathlib import Path

import pytest

from config import Config
from services.drive_service import DriveService


TEST_PROJECT_FOLDER_ID = os.getenv("TEST_PROJECT_FOLDER_ID")


@pytest.mark.integration
def test_ensure_project_scope_templates_idempotent():
    """Integration test: apply templates twice and verify idempotency.

    Requirements:
      - Set `TEST_PROJECT_FOLDER_ID` env var to the project folder id to test against.
      - Ensure `GOOGLE_APPLICATION_CREDENTIALS` is set and valid.
    """
    if not TEST_PROJECT_FOLDER_ID:
        pytest.skip("Set TEST_PROJECT_FOLDER_ID to run integration test")

    cfg = Config.from_env()
    ds = DriveService(cfg)

    template_dir = Path(__file__).resolve().parents[1] / "Templates"
    assert template_dir.exists(), f"Templates directory not found: {template_dir}"

    # First run: should upload templates (or skip if already present)
    summary1 = ds.ensure_project_scope_templates(TEST_PROJECT_FOLDER_ID, template_dir=template_dir)
    assert isinstance(summary1, dict)
    assert "files_uploaded" in summary1 and "files_skipped" in summary1

    # Second run: should be idempotent, no new uploads
    summary2 = ds.ensure_project_scope_templates(TEST_PROJECT_FOLDER_ID, template_dir=template_dir)
    assert isinstance(summary2, dict)
    # On second run, no files should be uploaded (they should be skipped)
    assert summary2["files_uploaded"] == 0
    assert summary2["files_skipped"] >= summary1["files_uploaded"]
