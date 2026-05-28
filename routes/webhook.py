import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from services.drive_service import DriveService
from services.folder_structure import PROJECT_FOLDER_TREE

webhook_bp = Blueprint("webhook", __name__)
logger = logging.getLogger(__name__)

MAX_FOLDER_NAME_LENGTH = 255
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_job_lock = threading.Lock()
_job_executor: ThreadPoolExecutor | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

    logger.info("Payload validation succeeded project=%s customer=%s", project_name, customer)
    return project_name, customer


def _is_duplicate(project_name: str, customer: str) -> bool:
    key = f"{project_name}_{customer}".replace(" ", "_")
    os.makedirs("processed", exist_ok=True)
    file_path = os.path.join("processed", f"{key}.txt")

    if os.path.exists(file_path):
        logger.info("Duplicate request ignored for %s", key)
        return True

    with open(file_path, "w", encoding="utf-8") as file:
        file.write("processed")

    return False


def _get_executor(max_workers: int) -> ThreadPoolExecutor:
    global _job_executor
    with _job_lock:
        if _job_executor is None:
            _job_executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="folder-job")
        return _job_executor


def _job_id(project_name: str, customer: str) -> str:
    raw_key = f"{project_name}\0{customer}".encode("utf-8")
    return hashlib.sha256(raw_key).hexdigest()[:32]


def _job_path(processed_dir: Path, job_id: str) -> Path:
    return processed_dir / f"{job_id}.json"


def _read_job_state(processed_dir: Path, job_id: str) -> dict[str, Any] | None:
    path = _job_path(processed_dir, job_id)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read job state file job_id=%s", job_id, exc_info=True)
        return None

    return data if isinstance(data, dict) else None


def _write_job_state(processed_dir: Path, job_id: str, state: dict[str, Any]) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = _job_path(processed_dir, job_id)
    tmp_path = path.with_suffix(".tmp")
    state = {**state, "jobId": job_id, "updatedAt": _utc_now()}

    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _begin_job(processed_dir: Path, project_name: str, customer: str) -> tuple[str, dict[str, Any], bool]:
    job_id = _job_id(project_name, customer)

    with _job_lock:
        existing = _read_job_state(processed_dir, job_id)
        if existing and existing.get("status") in {"pending", "running", "completed"}:
            return job_id, existing, False

        state = {
            "status": "pending",
            "project": project_name,
            "customer": customer,
            "createdAt": _utc_now(),
        }
        _write_job_state(processed_dir, job_id, state)
        return job_id, state, True


def _create_project_tree(config: Any, project_name: str, customer: str) -> dict[str, Any]:
    drive_service = DriveService(config)

    projects_folder_id = getattr(config, "drive_projects_folder_id", None)
    if projects_folder_id:
        demo_projects_folder = drive_service.get_folder_by_id(projects_folder_id)
        logger.info(
            "Using configured Shared Drive Projects folder id=%s name=%s",
            demo_projects_folder.get("id"),
            demo_projects_folder.get("name"),
        )
    else:
        demo_projects_folder = drive_service.get_or_create_folder(
            name=config.demo_projects_folder_name,
            parent_id=config.demo_projects_parent_id,
        )

    project_folder = drive_service.get_or_create_folder(
        name=project_name,
        parent_id=demo_projects_folder["id"],
        description=f"Customer: {customer}",
    )

    summary = drive_service.ensure_folder_tree(
        parent_id=project_folder["id"],
        folder_tree=PROJECT_FOLDER_TREE,
    )

    copy_summary = drive_service.copy_project_scope_files(
        project_folder_id=project_folder["id"],
        project_name=project_name,
        template_root_folder_name=getattr(config, "drive_template_root_folder_name", "ITA-XXX_AI_Enebelment"),
        template_folder_name=getattr(config, "drive_template_folder_name", "Template"),
        template_root_folder_id=getattr(config, "drive_template_root_folder_id", None),
        template_folder_id=getattr(config, "drive_template_folder_id", None),
    )

    return {
        "project_folder": project_folder,
        "summary": summary,
        "copy_summary": copy_summary,
    }


def _mark_job_completed(config: Any, job_id: str, project_name: str, customer: str, result: dict[str, Any]) -> None:
    project_folder = result["project_folder"]
    summary = result["summary"]
    copy_summary = result.get("copy_summary", {})
    _write_job_state(
        config.processed_dir,
        job_id,
        {
            "status": "completed",
            "project": project_name,
            "customer": customer,
            "folderId": project_folder.get("id"),
            "folderName": project_folder.get("name"),
            "webViewLink": project_folder.get("webViewLink"),
            "createdFolders": summary["created_count"],
            "existingFolders": summary["existing_count"],
            "copiedFiles": copy_summary.get("copied_count", 0),
            "existingFiles": copy_summary.get("existing_count", 0),
            "completedAt": _utc_now(),
        },
    )


def _run_project_tree_job(config: Any, job_id: str, project_name: str, customer: str) -> None:
    _write_job_state(
        config.processed_dir,
        job_id,
        {
            "status": "running",
            "project": project_name,
            "customer": customer,
            "startedAt": _utc_now(),
        },
    )

    try:
        result = _create_project_tree(config, project_name, customer)
    except Exception as error:
        logger.exception("Folder setup failed job_id=%s project=%s", job_id, project_name)
        _write_job_state(
            config.processed_dir,
            job_id,
            {
                "status": "failed",
                "project": project_name,
                "customer": customer,
                "error": str(error),
                "failedAt": _utc_now(),
            },
        )
        return

    summary = result["summary"]
    logger.info(
        "Folder setup complete job_id=%s project=%s created=%s existing=%s copied=%s existing_files=%s",
        job_id,
        project_name,
        summary["created_count"],
        summary["existing_count"],
        result.get("copy_summary", {}).get("copied_count", 0),
        result.get("copy_summary", {}).get("existing_count", 0),
    )
    _mark_job_completed(config, job_id, project_name, customer, result)


def _mark_job_failed(config: Any, job_id: str, project_name: str, customer: str, error: Exception) -> None:
    _write_job_state(
        config.processed_dir,
        job_id,
        {
            "status": "failed",
            "project": project_name,
            "customer": customer,
            "error": str(error),
            "failedAt": _utc_now(),
        },
    )


@webhook_bp.post("/webhook/after-insert")
def after_insert_webhook():
    print("WEBHOOK RECEIVED")
    logger.info("WEBHOOK RECEIVED")

    payload = request.get_json(silent=True)
    print(payload)
    logger.info("WEBHOOK PAYLOAD %s", payload)

    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "Invalid JSON body"}), 400

    try:
        project_name, customer = _validate_payload(payload)
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400

    config = current_app.config["APP_CONFIG"]
    job_id, job_state, should_start = _begin_job(config.processed_dir, project_name, customer)

    if not should_start:
        status = job_state.get("status", "unknown")
        logger.info("Duplicate webhook ignored job_id=%s status=%s", job_id, status)
        return jsonify({
            "status": status,
            "message": "Project folder setup already completed" if status == "completed" else "Project folder setup already in progress",
            "jobId": job_id,
        }), 200 if status == "completed" else 202

    logger.info("Accepted folder setup job_id=%s project=%s customer=%s", job_id, project_name, customer)

    if config.webhook_async:
        executor = _get_executor(config.folder_job_workers)
        executor.submit(_run_project_tree_job, config, job_id, project_name, customer)
        return jsonify({
            "status": "accepted",
            "message": "Project folder setup started",
            "jobId": job_id,
            "statusUrl": f"/webhook/jobs/{job_id}",
        }), 202

    _write_job_state(
        config.processed_dir,
        job_id,
        {
            "status": "running",
            "project": project_name,
            "customer": customer,
            "startedAt": _utc_now(),
        },
    )
    try:
        result = _create_project_tree(config, project_name, customer)
    except Exception as error:
        _mark_job_failed(config, job_id, project_name, customer, error)
        raise

    _mark_job_completed(config, job_id, project_name, customer, result)
    summary = result["summary"]
    copy_summary = result.get("copy_summary", {})

    return jsonify({
        "status": "success",
        "message": "Folder created",
        "jobId": job_id,
        "project": project_name,
        "customer": customer,
        "createdFolders": summary["created_count"],
        "existingFolders": summary["existing_count"],
        "copiedFiles": copy_summary.get("copied_count", 0),
        "existingFiles": copy_summary.get("existing_count", 0),
    }), 201


@webhook_bp.post("/webhook")
def webhook_probe():
    print("WEBHOOK RECEIVED")
    logger.info("WEBHOOK RECEIVED")

    data = request.get_json(silent=True)
    print(data)
    logger.info("WEBHOOK PAYLOAD %s", data)

    return jsonify({"status": "received"}), 200


@webhook_bp.get("/webhook/jobs/<job_id>")
def get_webhook_job(job_id: str):
    if not JOB_ID_PATTERN.match(job_id):
        return jsonify({"status": "error", "message": "Invalid job id"}), 400

    config = current_app.config["APP_CONFIG"]
    job_state = _read_job_state(config.processed_dir, job_id)
    if not job_state:
        return jsonify({"status": "error", "message": "Job not found"}), 404

    return jsonify(job_state), 200


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
