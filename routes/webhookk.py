# import logging
# import re

# from flask import Blueprint, current_app, jsonify, request

# from services.drive_service import DriveService
# from services.folder_structure import PROJECT_FOLDER_TREE


# webhook_bp = Blueprint("webhook", __name__)
# logger = logging.getLogger(__name__)

# MAX_FOLDER_NAME_LENGTH = 255
# CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# def _validate_payload(payload: dict) -> tuple[str, str]:
#     project_name = str(payload.get("ProjectName", "")).strip()
#     customer = str(payload.get("Customer", "")).strip()

#     if not project_name:
#         raise ValueError("ProjectName is required")
#     if not customer:
#         raise ValueError("Customer is required")
#     if len(project_name) > MAX_FOLDER_NAME_LENGTH:
#         raise ValueError("ProjectName is too long")
#     if CONTROL_CHARS.search(project_name):
#         raise ValueError("ProjectName contains invalid control characters")

#     return project_name, customer


# def _is_authorized() -> bool:
#     token = current_app.config["APP_CONFIG"].webhook_token
#     if not token:
#         return True

#     auth_header = request.headers.get("Authorization", "")
#     return auth_header == f"Bearer {token}"


# @webhook_bp.post("/webhook/after-insert")
# def after_insert_webhook():
#     if not _is_authorized():
#         return jsonify({"status": "error", "message": "Unauthorized"}), 401

#     payload = request.get_json(silent=True)
#     if not isinstance(payload, dict):
#         return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

#     try:
#         project_name, customer = _validate_payload(payload)
#     except ValueError as error:
#         return jsonify({"status": "error", "message": str(error)}), 400

#     logger.info("Received AFTER_INSERT webhook for project=%s customer=%s", project_name, customer)

#     config = current_app.config["APP_CONFIG"]
#     drive_service = DriveService(config)

#     demo_projects_folder = drive_service.get_or_create_folder(
#         name=config.demo_projects_folder_name,
#         parent_id=config.demo_projects_parent_id,
#     )
#     # Compute a project key used to prevent duplicate execution. Prefer an explicit ProjectNumber
#     # from the payload; fall back to a sanitized ProjectName.
#     project_number = payload.get("ProjectNumber") or payload.get("ProjectNo")
#     if project_number:
#         project_key_base = str(project_number).strip()
#     else:
#         # fallback: use a sanitized project name as the unique base
#         project_key_base = re.sub(r"[^0-9A-Za-z_-]", "_", project_name).strip()

#     # Create / reuse the main project folder using a project-specific key (ProjectNumber::ProjectName)
#     project_folder = drive_service.get_or_create_folder(
#         name=project_name,
#         parent_id=demo_projects_folder["id"],
#         description=f"Customer: {customer}",
#         project_key=f"{project_key_base}::{project_name}",
#     )
#     # If the project folder already existed and its description indicates the
#     # same customer, treat this as a duplicate request and ignore further processing.
#     if not project_folder.get("_was_created"):
#         desc = project_folder.get("description") or ""
#         expected = f"Customer: {customer}"
#         if desc == expected:
#             logger.info("Duplicate request ignored for project=%s customer=%s", project_name, customer)
#             return jsonify(
#                 {
#                     "status": "success",
#                     "message": "Duplicate request ignored",
#                     "project": project_name,
#                     "customer": customer,
#                     "demoProjectsFolderId": demo_projects_folder["id"],
#                     "projectFolderId": project_folder["id"],
#                     "createdFolders": 0,
#                     "existingFolders": 0,
#                 }
#             ), 200
#         # If description doesn't match, fall back to previous behavior for project numbers
#         if project_number:
#             logger.info("Project already exists, skipping subfolder creation for project=%s", project_name)
#             return jsonify(
#                 {
#                     "status": "success",
#                     "message": "Project already exists; skipped subfolder creation",
#                     "project": project_name,
#                     "customer": customer,
#                     "demoProjectsFolderId": demo_projects_folder["id"],
#                     "projectFolderId": project_folder["id"],
#                     "createdFolders": 0,
#                     "existingFolders": 0,
#                 }
#             ), 200

#     # Ensure child folders; pass the project_key_base so each child folder will use
#     # ProjectKeyBase::ChildFolderName as their unique key.
#     summary = drive_service.ensure_folder_tree(
#         parent_id=project_folder["id"],
#         folder_tree=PROJECT_FOLDER_TREE,
#         project_key=project_key_base,
#     )

#     logger.info(
#         "Folder setup complete for project=%s created=%s existing=%s",
#         project_name,
#         summary["created_count"],
#         summary["existing_count"],
#     )

#     return jsonify(
#         {
#             "status": "success",
#             "message": "Folder created",
#             "project": project_name,
#             "customer": customer,
#             "demoProjectsFolderId": demo_projects_folder["id"],
#             "projectFolderId": project_folder["id"],
#             "createdFolders": summary["created_count"],
#             "existingFolders": summary["existing_count"],
#         }
#     ), 201
