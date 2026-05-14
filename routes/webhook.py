import logging
import re
import os

from flask import Blueprint, current_app, jsonify, request

from services.drive_service import DriveService
from services.folder_structure import PROJECT_FOLDER_TREE

webhook_bp = Blueprint("webhook", __name__)
logger = logging.getLogger(__name__)

MAX_FOLDER_NAME_LENGTH = 255
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# =========================================================
# VALIDATE PAYLOAD
# =========================================================

def _validate_payload(payload: dict) -> tuple[str, str]:

    project_name = str(
        payload.get("ProjectName", "")
    ).strip()

    customer = str(
        payload.get("Customer", "")
    ).strip()

    if not project_name:
        raise ValueError("ProjectName is required")

    if not customer:
        raise ValueError("Customer is required")

    if len(project_name) > MAX_FOLDER_NAME_LENGTH:
        raise ValueError("ProjectName is too long")

    if CONTROL_CHARS.search(project_name):
        raise ValueError(
            "ProjectName contains invalid control characters"
        )

    return project_name, customer


# =========================================================
# DUPLICATE CHECK
# =========================================================

def _is_duplicate(
    project_name: str,
    customer: str
) -> bool:

    key = (
        f"{project_name}_{customer}"
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
    )

    os.makedirs("processed", exist_ok=True)

    file_path = os.path.join(
        "processed",
        f"{key}.txt"
    )

    if os.path.exists(file_path):

        logger.info(
            "Duplicate request ignored for %s",
            key
        )

        return True

    with open(file_path, "w") as file:
        file.write("processed")

    return False


# =========================================================
# MAIN WEBHOOK
# =========================================================

@webhook_bp.post("/webhook/after-insert")
def after_insert_webhook():

    try:

        print("=== WEBHOOK TRIGGERED ===")

        payload = request.get_json(silent=True)

        if not isinstance(payload, dict):

            return jsonify({
                "status": "error",
                "message": "Invalid JSON body"
            }), 400

        # =========================================
        # VALIDATION
        # =========================================

        try:

            project_name, customer = (
                _validate_payload(payload)
            )

        except ValueError as error:

            return jsonify({
                "status": "error",
                "message": str(error)
            }), 400

        # =========================================
        # DUPLICATE CHECK
        # =========================================

        if _is_duplicate(
            project_name,
            customer
        ):

            return jsonify({
                "status": "success",
                "message": "Duplicate request ignored"
            }), 200

        logger.info(
            "Processing project=%s customer=%s",
            project_name,
            customer
        )

        # =========================================
        # CONFIG
        # =========================================

        config = current_app.config["APP_CONFIG"]

        drive_service = DriveService(config)

        # =========================================
        # MAIN PROJECTS FOLDER
        # =========================================

        demo_projects_folder = (
            drive_service.get_or_create_folder(
                name=config.demo_projects_folder_name,
                parent_id=config.demo_projects_parent_id,
            )
        )

        logger.info(
            "Demo Projects Folder ID: %s",
            demo_projects_folder.get("id")
        )

        # =========================================
        # PROJECT FOLDER
        # =========================================

        project_folder = (
            drive_service.get_or_create_folder(
                name=project_name,
                parent_id=demo_projects_folder["id"],
                description=f"Customer: {customer}"
            )
        )

        logger.info(
            "Project Folder ID: %s",
            project_folder.get("id")
        )

        created_folders = 0
        existing_folders = 0
        uploaded_files = 0
        skipped_files = 0
        errors = 0

        # =========================================
        # CREATE SUBFOLDERS
        # =========================================

        if project_folder.get("_was_created"):

            logger.info(
                "Creating subfolders for project=%s",
                project_name
            )

            summary = (
                drive_service.ensure_folder_tree(
                    parent_id=project_folder["id"],
                    folder_tree=PROJECT_FOLDER_TREE
                )
            )

            created_folders = summary.get(
                "created_count",
                0
            )

            existing_folders = summary.get(
                "existing_count",
                0
            )

            errors = summary.get(
                "errors",
                0
            )

            logger.info(
                "Folder creation completed created=%s existing=%s errors=%s",
                created_folders,
                existing_folders,
                errors
            )

            # =========================================
            # TEMPLATE UPLOAD
            # =========================================

            try:

                tpl_summary = (
                    drive_service.ensure_project_scope_templates(
                        project_folder["id"]
                    )
                    or {}
                )

                uploaded_files = tpl_summary.get(
                    "files_uploaded",
                    0
                )

                skipped_files = tpl_summary.get(
                    "files_skipped",
                    0
                )

                errors += tpl_summary.get(
                    "errors",
                    0
                )

                logger.info(
                    "Templates uploaded=%s skipped=%s errors=%s",
                    uploaded_files,
                    skipped_files,
                    errors
                )

            except Exception as template_error:

                logger.exception(
                    "Template upload failed"
                )

                errors += 1

        else:

            logger.info(
                "Project folder already exists"
            )

        # =========================================
        # FINAL RESPONSE
        # =========================================

        response = {
            "status": "success",
            "message": "Folder processed successfully",
            "project": project_name,
            "customer": customer,
            "projectFolderId": project_folder.get("id"),
            "createdFolders": created_folders,
            "existingFolders": existing_folders,
            "uploadedFiles": uploaded_files,
            "skippedFiles": skipped_files,
            "errors": errors,
        }

        return jsonify(response), 201

    except Exception as error:

        current_app.logger.exception(
            "Webhook processing failed"
        )

        return jsonify({
            "status": "error",
            "message": str(error)
        }), 500


# =========================================================
# CREATE PROJECT API
# =========================================================

@webhook_bp.post("/webhook/create-project")
def create_project():

    try:

        payload = request.get_json(silent=True)

        if not isinstance(payload, dict):

            return jsonify({
                "status": "error",
                "message": "Invalid JSON body"
            }), 400

        try:

            project_name, customer = (
                _validate_payload(payload)
            )

        except ValueError as error:

            return jsonify({
                "status": "error",
                "message": str(error)
            }), 400

        if _is_duplicate(
            project_name,
            customer
        ):

            return jsonify({
                "status": "success",
                "message": "Duplicate request ignored"
            }), 200

        logger.info(
            "Creating project=%s customer=%s",
            project_name,
            customer
        )

        config = current_app.config["APP_CONFIG"]

        drive_service = DriveService(config)

        project_folder = (
            drive_service.create_project_folder(
                project_name=project_name,
                customer_name=customer,
                projects_folder_name=getattr(
                    config,
                    "demo_projects_folder_name",
                    "Projects"
                ),
                description=f"Customer: {customer}",
            )
        )

        return jsonify({
            "status": "success",
            "message": "Project folder created",
            "folderId": project_folder.get("id"),
            "folderName": project_folder.get("name"),
            "webViewLink": project_folder.get("webViewLink"),
        }), 201

    except Exception as error:

        current_app.logger.exception(
            "Create project API failed"
        )

        return jsonify({
            "status": "error",
            "message": str(error)
        }), 500