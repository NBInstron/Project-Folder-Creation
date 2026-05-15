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


def _validate_payload(payload: dict) -> tuple[str, str]:
    project_name = str(payload.get("ProjectName", "")).strip()
    customer = str(payload.get("Customer", "")).strip()

    if not project_name:
        raise ValueError("ProjectName is required")
    if not customer:
        raise ValueError("Customer is required")
    if len(project_name) > MAX_FOLDER_NAME_LENGTH:
        raise ValueError("ProjectName is too long")
    if CONTROL_CHARS.search(project_name):
        raise ValueError("ProjectName contains invalid control characters")

    return project_name, customer


def _is_duplicate(project_name: str, customer: str) -> bool:
    key = f"{project_name}_{customer}".replace(" ", "_")
    os.makedirs("processed", exist_ok=True)
    file_path = os.path.join("processed", f"{key}.txt")

    if os.path.exists(file_path):
        logger.info("Duplicate request ignored for %s", key)
        return True

    with open(file_path, "w") as f:
        f.write("processed")

    return False


@webhook_bp.post("/webhook/after-insert")
def after_insert_webhook():
    print("=== WEBHOOK TRIGGERED ===")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

    try:
        project_name, customer = _validate_payload(payload)
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400

    # 🚨 IMPORTANT: STOP DUPLICATES BEFORE ANY DRIVE CALL
    if _is_duplicate(project_name, customer):
        return jsonify({
            "status": "success",
            "message": "Duplicate request ignored"
        }), 200

    logger.info("Processing project=%s customer=%s", project_name, customer)

    config = current_app.config["APP_CONFIG"]
    drive_service = DriveService(config)

    # Main parent folder
    demo_projects_folder = drive_service.get_or_create_folder(
        name=config.demo_projects_folder_name,
        parent_id=config.demo_projects_parent_id,
    )

    # Create main project folder (safe now)
    project_folder = drive_service.get_or_create_folder(
        name=project_name,
        parent_id=demo_projects_folder["id"],
        description=f"Customer: {customer}"
    )

    # Create subfolders safely
    summary = drive_service.ensure_folder_tree(
        parent_id=project_folder["id"],
        folder_tree=PROJECT_FOLDER_TREE
    )

    logger.info(
        "Folder setup complete project=%s created=%s existing=%s",
        project_name,
        summary["created_count"],
        summary["existing_count"],
    )

    return jsonify({
        "status": "success",
        "message": "Folder created",
        "project": project_name,
        "customer": customer,
        "createdFolders": summary["created_count"],
        "existingFolders": summary["existing_count"]
    }), 201


@webhook_bp.post("/webhook/create-project")
def create_project():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

    try:
        project_name, customer = _validate_payload(payload)
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400

    if _is_duplicate(project_name, customer):
        return jsonify({"status": "success", "message": "Duplicate request ignored"}), 200

    logger.info("Creating project folder for=%s customer=%s", project_name, customer)

    config = current_app.config["APP_CONFIG"]
    drive_service = DriveService(config)

    project_folder = drive_service.create_project_folder(
        project_name=project_name,
        customer_name=customer,
        projects_folder_name=getattr(config, "demo_projects_folder_name", "Projects"),
        description=f"Customer: {customer}",
    )

    return jsonify({
        "status": "success",
        "message": "Project folder created",
        "folderId": project_folder.get("id"),
        "folderName": project_folder.get("name"),
        "webViewLink": project_folder.get("webViewLink"),
    }), 201