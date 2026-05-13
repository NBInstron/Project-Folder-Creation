"""CLI to apply project scope templates to a named project folder.

Usage:
  python scripts/apply_templates.py --project "MyProj-Customer"

The script locates the top-level `Projects` folder (configurable via env DEMO_PROJECTS_FOLDER_NAME
or DEMO_PROJECTS_PARENT_ID for a parent id), finds the named project folder under it, and calls
`DriveService.ensure_project_scope_templates` to create folders and upload files from local `Templates/`.
"""
import argparse
import logging
import sys
from pathlib import Path

from config import Config
from services.drive_service import DriveService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply project scope templates to a project folder")
    parser.add_argument("--project", "-p", required=True, help="Project folder name (exact name under Projects)")
    parser.add_argument("--templates", "-t", help="Optional local templates directory (defaults to Templates/)")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger("apply_templates")

    ds = DriveService(cfg)

    # Locate Projects folder
    projects_folder = ds.find_folder(cfg.demo_projects_folder_name, parent_id=cfg.demo_projects_parent_id)
    if not projects_folder:
        logger.error("Top-level Projects folder '%s' not found", cfg.demo_projects_folder_name)
        return 2

    # Find project folder by name under Projects
    project_folder = ds.find_folder(args.project, parent_id=projects_folder["id"]) if projects_folder else None
    if not project_folder:
        logger.error("Project folder '%s' not found under '%s'", args.project, cfg.demo_projects_folder_name)
        return 3

    # Determine templates dir
    template_dir = Path(args.templates) if args.templates else None

    logger.info("Applying templates to project '%s' (id=%s)", project_folder["name"], project_folder["id"])
    summary = ds.ensure_project_scope_templates(project_folder_id=project_folder["id"], template_dir=template_dir)

    logger.info("Summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
