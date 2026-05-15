import os

from flask import Flask, jsonify
from googleapiclient.errors import HttpError

from config import Config
from logging_config import configure_logging
from routes.webhook import webhook_bp
from services.drive_service import DriveAuthError


def create_app() -> Flask:

    # Load configuration
    config = Config.from_env()

    # Configure logging
    configure_logging(config.log_file)

    # Create Flask app
    app = Flask(__name__)

    # Store config in app
    app.config.from_mapping(
        APP_CONFIG=config
    )

    # Register Blueprints
    app.register_blueprint(webhook_bp)

    app.logger.info("Webhook blueprint registered successfully")
    app.logger.info("Application started")

    # -----------------------------
    # Health Check Route
    # -----------------------------
    @app.get("/")
    def home():
        return jsonify({
            "status": "running",
            "message": "Folder Creation API Running Successfully"
        }), 200

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok"
        }), 200

    # -----------------------------
    # Error Handlers
    # -----------------------------
    @app.errorhandler(DriveAuthError)
    def handle_drive_auth_error(error: DriveAuthError):

        app.logger.exception("Google Drive authentication failed")

        return jsonify({
            "status": "error",
            "message": str(error)
        }), 500

    @app.errorhandler(HttpError)
    def handle_google_api_error(error: HttpError):

        app.logger.exception("Google Drive API request failed")

        return jsonify({
            "status": "error",
            "message": "Google Drive API failure"
        }), 502

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):

        app.logger.exception("Unhandled application error")

        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

    return app


# Create app instance for Gunicorn
app = create_app()


# ------------------------------------------------
# Local Development Server
# ------------------------------------------------
if __name__ == "__main__":

    config = app.config["APP_CONFIG"]

    # Render provides PORT automatically
    port = int(os.environ.get("PORT", config.flask_port))

    app.logger.info(f"Starting Flask server on port {port}")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=config.flask_debug
    )