"""EduSaaS – Application SaaS de gestion scolaire multi-école avec module BI.

Factory Flask : configuration, base de données, authentification, protection CSRF
et enregistrement des blueprints.
"""
import os
import secrets

from flask import Flask, abort, request, session
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///edusaas.db")
    # Certains hébergeurs (Heroku, Render, Neon…) fournissent "postgres://"
    # alors que SQLAlchemy attend "postgresql://".
    # On force le pilote psycopg2 (SQLAlchemy 2.1 utilise psycopg 3 par défaut).
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    return url


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (enregistre les modèles)
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .bi import bp as bi_bp
    from .platform import bp as platform_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(platform_bp)

    # ---- Protection CSRF minimale (jeton de session vérifié sur chaque POST) ----
    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not app.config.get("TESTING_NO_CSRF"):
            token = session.get("_csrf")
            if not token or token != request.form.get("_csrf"):
                abort(400, "Jeton CSRF invalide")

    @app.context_processor
    def inject_globals():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        from .models import ROLE_LABELS
        from .bi import DASHBOARDS

        def bi_menu(role):
            return [(k, v) for k, v in DASHBOARDS.items() if role in v[3]]
        return {"csrf_token": session["_csrf"], "ROLE_LABELS": ROLE_LABELS, "bi_menu": bi_menu}

    from flask import render_template

    errors = {400: "Requête invalide.", 403: "Accès refusé : votre rôle ne permet pas d'accéder à cette page.",
              404: "Page introuvable.", 500: "Erreur interne du serveur."}
    for code, msg in errors.items():
        app.register_error_handler(
            code, lambda e, code=code, msg=msg: (render_template("error.html", code=code, msg=msg), code))

    @app.template_filter("money")
    def money(v):
        return f"{(v or 0):,.0f} DT".replace(",", " ")

    @app.template_filter("pct")
    def pct(v):
        return f"{(v or 0):.1f} %"

    with app.app_context():
        db.create_all()

    return app
