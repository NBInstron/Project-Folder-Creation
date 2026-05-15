import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app import create_app


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


if __name__ == "__main__":
    unittest.main()
