import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from services.drive_service import DriveAuthError, DriveService, get_or_create_folder


def make_list_response(files):
    m = Mock()
    m.execute.return_value = {"files": files}
    return m


class DriveServiceTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test")

    def make_drive_service(self, service):
        drive_service = DriveService.__new__(DriveService)
        drive_service._service = service
        drive_service._logger = self.logger
        return drive_service

    @patch("services.drive_service.build")
    @patch("services.drive_service.service_account.Credentials.from_service_account_file")
    def test_build_service_uses_delegated_user_when_configured(self, mock_from_file, mock_build):
        credentials = Mock()
        delegated_credentials = Mock()
        credentials.with_subject.return_value = delegated_credentials
        mock_from_file.return_value = credentials
        mock_build.return_value = Mock()
        drive_service = self.make_drive_service(Mock())

        with TemporaryDirectory() as temp_dir:
            credentials_path = Path(temp_dir) / "credentials.json"
            credentials_path.write_text("{}", encoding="utf-8")
            config = SimpleNamespace(
                credentials_file=credentials_path,
                google_impersonated_user="owner@example.com",
            )

            drive_service._build_service(config)

        credentials.with_subject.assert_called_once_with("owner@example.com")
        mock_build.assert_called_once_with(
            "drive",
            "v3",
            credentials=delegated_credentials,
            cache_discovery=False,
        )

    def test_build_service_rejects_oauth_mode(self):
        drive_service = self.make_drive_service(Mock())

        with TemporaryDirectory() as temp_dir:
            config = SimpleNamespace(
                google_auth_mode="oauth",
                credentials_file=Path(temp_dir) / "credentials.json",
                google_impersonated_user=None,
            )

            with self.assertRaises(DriveAuthError):
                drive_service._build_service(config)

    def test_copy_file_if_missing_skips_existing_file(self):
        service = Mock()
        files = Mock()
        service.files.return_value = files
        files.list.return_value = make_list_response([
            {"id": "file1", "name": "ITP-153_CLT_03_R01.xlsm"}
        ])
        drive_service = self.make_drive_service(service)

        result = drive_service.copy_file_if_missing(
            "template-file",
            "ITP-153_CLT_03_R01.xlsm",
            "scope-folder",
        )

        self.assertEqual(result["id"], "file1")
        self.assertEqual(result["_copy_status"], "existing")
        files.copy.assert_not_called()

    def test_copy_file_if_missing_copies_new_file(self):
        service = Mock()
        files = Mock()
        service.files.return_value = files
        files.list.return_value = make_list_response([])
        files.copy.return_value.execute.return_value = {
            "id": "file2",
            "name": "ITP-153_SOW_R01.docx",
            "webViewLink": "https://drive.mock/file2",
        }
        drive_service = self.make_drive_service(service)

        result = drive_service.copy_file_if_missing(
            "template-file",
            "ITP-153_SOW_R01.docx",
            "scope-folder",
        )

        self.assertEqual(result["id"], "file2")
        self.assertEqual(result["_copy_status"], "copied")
        copy_kwargs = files.copy.call_args.kwargs
        self.assertEqual(copy_kwargs["fileId"], "template-file")
        self.assertEqual(
            copy_kwargs["body"],
            {"name": "ITP-153_SOW_R01.docx", "parents": ["scope-folder"]},
        )
        self.assertTrue(copy_kwargs["supportsAllDrives"])

    def test_find_file_scopes_search_to_shared_drive_when_configured(self):
        service = Mock()
        files = Mock()
        service.files.return_value = files
        files.list.return_value = make_list_response([])
        drive_service = self.make_drive_service(service)
        drive_service._shared_drive_id = "shared-drive-id"

        result = drive_service.find_file("Scope.docx", "scope-folder")

        self.assertIsNone(result)
        list_kwargs = files.list.call_args.kwargs
        self.assertTrue(list_kwargs["supportsAllDrives"])
        self.assertTrue(list_kwargs["includeItemsFromAllDrives"])
        self.assertEqual(list_kwargs["corpora"], "drive")
        self.assertEqual(list_kwargs["driveId"], "shared-drive-id")

    def test_copy_template_file_if_missing_tries_numbered_source_fallback(self):
        drive_service = self.make_drive_service(Mock())
        drive_service.find_file = Mock(side_effect=[
            None,
            None,
            None,
            {"id": "template-file", "name": "01. <ProjectName>_CLT_03_R01.xlsx"},
        ])
        drive_service.copy_file_if_missing = Mock(return_value={
            "id": "copied-file",
            "name": "ITP-153_CLT_03_R01.xlsm",
            "_copy_status": "copied",
        })

        result = drive_service.copy_template_file_if_missing(
            source_template_name=(
                "<ProjectName>_CLT_03_R01.xlsm",
                "<ProjectName>_CLT_03_R01.xlsx",
                "01. <ProjectName>_CLT_03_R01.xlsx",
            ),
            drive_name="ITP-153_CLT_03_R01.xlsm",
            source_folder_id="template-folder",
            parent_id="scope-folder",
        )

        self.assertEqual(result["id"], "copied-file")
        searched_names = [call.args[0] for call in drive_service.find_file.call_args_list[1:3]]
        self.assertEqual(searched_names, [
            "<ProjectName>_CLT_03_R01.xlsm",
            "<ProjectName>_CLT_03_R01.xlsx",
        ])
        drive_service.copy_file_if_missing.assert_called_once_with(
            "template-file",
            "ITP-153_CLT_03_R01.xlsm",
            "scope-folder",
        )

    def test_copy_template_file_if_missing_can_find_source_by_fallback_terms(self):
        drive_service = self.make_drive_service(Mock())
        drive_service.find_file = Mock(side_effect=[None, None, None])
        drive_service.list_files = Mock(return_value=[
            {"id": "other-file", "name": "Readme.docx"},
            {"id": "template-file", "name": "01. ProjectName_CLT_03_R01.xlsx"},
        ])
        drive_service.copy_file_if_missing = Mock(return_value={
            "id": "copied-file",
            "name": "ITP-153_CLT_03_R01.xlsm",
            "_copy_status": "copied",
        })

        result = drive_service.copy_template_file_if_missing(
            source_template_name=("missing.xlsm", "missing.xlsx"),
            drive_name="ITP-153_CLT_03_R01.xlsm",
            source_folder_id="template-folder",
            parent_id="scope-folder",
            required_terms=("clt_03_r01",),
            allowed_extensions=(".xlsm", ".xlsx"),
        )

        self.assertEqual(result["id"], "copied-file")
        drive_service.list_files.assert_called_once_with("template-folder")
        drive_service.copy_file_if_missing.assert_called_once_with(
            "template-file",
            "ITP-153_CLT_03_R01.xlsm",
            "scope-folder",
        )

    def test_get_or_create_folder_creates_then_skips(self):
        service = Mock()
        files = Mock()
        service.files.return_value = files

        # First list call: no folders. Second list call: folder exists.
        files.list.side_effect = [
            make_list_response([]),
            make_list_response([{"id": "f1", "name": "Design"}]),
        ]
        files.create.return_value.execute.return_value = {"id": "f1", "name": "Design"}

        created_map = {}
        folder = get_or_create_folder(service, "Design", parent_id="p1", logger=self.logger, created_map=created_map)
        self.assertEqual(folder["id"], "f1")

        # Second call should find existing (list returns folder) and not call create again
        folder2 = get_or_create_folder(service, "Design", parent_id="p1", logger=self.logger, created_map={})
        self.assertEqual(folder2["id"], "f1")
        self.assertEqual(files.create.call_count, 1)

    def test_copy_project_scope_files_copies_directly_to_project_scope(self):
        drive_service = DriveService.__new__(DriveService)
        drive_service.get_or_create_folder = Mock(side_effect=[
            {"id": "project-section", "name": "05. Project"},
            {"id": "scope-folder", "name": "03. Project Scope"},
        ])
        drive_service.resolve_template_folder = Mock(return_value={"id": "template-folder", "name": "Template"})
        drive_service.copy_template_file_if_missing = Mock(side_effect=[
            {"id": "file1", "name": "01.MyProject_CLT_03_R01.xlsm", "_copy_status": "copied"},
            {"id": "file2", "name": "02.MyProject_SOW.docx", "_copy_status": "existing"},
        ])

        summary = drive_service.copy_project_scope_files(
            project_folder_id="project-root",
            project_name="MyProject",
        )

        drive_service.get_or_create_folder.assert_any_call("05. Project", parent_id="project-root")
        drive_service.get_or_create_folder.assert_any_call(
            "03. Project Scope",
            parent_id="project-section",
        )
        source_names = [
            call.kwargs["source_template_name"]
            for call in drive_service.copy_template_file_if_missing.call_args_list
        ]
        copied_names = [
            call.kwargs["drive_name"]
            for call in drive_service.copy_template_file_if_missing.call_args_list
        ]
        parent_ids = [
            call.kwargs["parent_id"]
            for call in drive_service.copy_template_file_if_missing.call_args_list
        ]
        self.assertEqual(source_names, [
            (
                "<ProjectName>_CLT_03_R01.xlsm",
                "<ProjectName>_CLT_03_R01.xlsx",
                "01. <ProjectName>_CLT_03_R01.xlsm",
                "01. <ProjectName>_CLT_03_R01.xlsx",
                "01.<ProjectName>_CLT_03_R01.xlsm",
                "01.<ProjectName>_CLT_03_R01.xlsx",
            ),
            (
                "<ProjectName>_SOW_R01.docx",
                "<ProjectName>_SOW_R01.docx",
                "02. <ProjectName>_SOW_R01.docx",
                "02. <ProjectName>_SOW_R01.docx",
                "02.<ProjectName>_SOW_R01.docx",
                "02.<ProjectName>_SOW_R01.docx",
            ),
        ])
        self.assertEqual(copied_names, [
            "01.MyProject_CLT_03_R01.xlsm",
            "02.MyProject_SOW_R01.docx",
        ])
        self.assertEqual(parent_ids, ["scope-folder", "scope-folder"])
        self.assertEqual(summary["copied_count"], 1)
        self.assertEqual(summary["existing_count"], 1)

    def test_in_memory_protection_prevents_duplicate_create(self):
        service = Mock()
        files = Mock()
        service.files.return_value = files

        # Only one list() call expected; after create the created_map prevents further list/create
        files.list.return_value = make_list_response([])
        files.create.return_value.execute.return_value = {"id": "f2", "name": "Docs"}

        created_map = {}
        f1 = get_or_create_folder(service, "Docs", parent_id="p1", logger=self.logger, created_map=created_map)
        f2 = get_or_create_folder(service, "Docs", parent_id="p1", logger=self.logger, created_map=created_map)

        self.assertEqual(f1["id"], "f2")
        self.assertEqual(f2["id"], "f2")
        # create should have been called only once
        self.assertEqual(files.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
