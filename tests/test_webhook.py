import unittest
from unittest.mock import Mock, patch

from app import create_app


class WebhookRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()

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
