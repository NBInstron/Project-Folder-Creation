import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app import create_app
from routes.webhook import _create_project_tree


class WebhookRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

    @patch("routes.webhook._get_executor")
    def test_after_insert_starts_async_job(self, mock_get_executor):
        with TemporaryDirectory() as temp_dir:
            self.app.config["APP_CONFIG"] = replace(
                self.app.config["APP_CONFIG"],
                webhook_async=True,
                processed_dir=Path(temp_dir),
            )
            mock_executor = Mock()
            mock_get_executor.return_value = mock_executor

            resp = self.client.post(
                "/webhook/after-insert",
                json={"ProjectName": "MyProject", "Customer": "ACME"},
            )

            self.assertEqual(resp.status_code, 202)
            data = resp.get_json()
            self.assertEqual(data["status"], "accepted")
            self.assertIn("jobId", data)
            mock_executor.submit.assert_called_once()

            status_resp = self.client.get(f"/webhook/jobs/{data['jobId']}")
            self.assertEqual(status_resp.status_code, 200)
            self.assertEqual(status_resp.get_json()["status"], "pending")

    @patch("routes.webhook._is_duplicate", return_value=False)
    @patch("routes.webhook.DriveService")
    def test_create_project_route_calls_drive_service(self, mock_drive_service_cls, mock_dup):
        # Arrange: mock DriveService instance and its create_project_folder
        mock_drive = Mock()
        mock_drive.create_project_folder.return_value = {
            "id": "proj123",
            "name": "MyProject-ACME",
            "webViewLink": "https://drive.mock/proj123",
        }
        mock_drive_service_cls.return_value = mock_drive

        # Act
        resp = self.client.post(
            "/webhook/create-project",
            json={"ProjectName": "MyProject", "Customer": "ACME"},
        )

        # Assert
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["folderId"], "proj123")
        mock_drive.create_project_folder.assert_called_once()

    @patch("routes.webhook.DriveService")
    def test_create_project_tree_copies_project_scope_files(self, mock_drive_service_cls):
        mock_drive = Mock()
        mock_drive.get_folder_by_id.return_value = {"id": "demo-projects", "name": "Projects"}
        mock_drive.get_or_create_folder.side_effect = [
            {"id": "project-root", "name": "MyProject"},
        ]
        mock_drive.ensure_folder_tree.return_value = {"created_count": 1, "existing_count": 2}
        mock_drive.copy_project_scope_files.return_value = {
            "copied_count": 2,
            "existing_count": 0,
        }
        mock_drive_service_cls.return_value = mock_drive

        result = _create_project_tree(
            self.app.config["APP_CONFIG"],
            project_name="MyProject",
            customer="ACME",
        )

        mock_drive.get_folder_by_id.assert_called_once_with(
            self.app.config["APP_CONFIG"].drive_projects_folder_id
        )
        mock_drive.copy_project_scope_files.assert_called_once_with(
            project_folder_id="project-root",
            project_name="MyProject",
            template_root_folder_name=self.app.config["APP_CONFIG"].drive_template_root_folder_name,
            template_folder_name=self.app.config["APP_CONFIG"].drive_template_folder_name,
            template_root_folder_id=self.app.config["APP_CONFIG"].drive_template_root_folder_id,
            template_folder_id=self.app.config["APP_CONFIG"].drive_template_folder_id,
        )
        self.assertEqual(result["copy_summary"]["copied_count"], 2)


if __name__ == "__main__":
    unittest.main()
