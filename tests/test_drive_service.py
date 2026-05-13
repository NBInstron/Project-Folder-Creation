import logging
import unittest
from unittest.mock import Mock

from services.drive_service import get_or_create_folder


def make_list_response(files):
    m = Mock()
    m.execute.return_value = {"files": files}
    return m


class DriveServiceTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test")

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
