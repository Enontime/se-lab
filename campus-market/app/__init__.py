"""Flask application factory. Pure business services work without Flask installed."""
from __future__ import annotations

import os
from pathlib import Path


def create_app(test_config: dict | None = None):
    from flask import Flask, render_template
    from app.api.routes import register_routes
    from app.services.market import Market

    app = Flask(__name__)
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE=str(instance / "campus_market.sqlite3"),
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    service = Market(app.config["DATABASE"])
    service.initialize()
    app.extensions["campus_market_service"] = service
    register_routes(app)

    @app.get("/")
    def home():
        """Serve the 11-screen Web UI; the existing /api routes stay unchanged."""
        return render_template("index.html")

    return app
