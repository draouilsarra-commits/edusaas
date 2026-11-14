from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import db
from .models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(request.args.get("next") or url_for("main.home"))
        flash("Identifiants incorrects.", "danger")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/profil/mot-de-passe", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        if not current_user.check_password(request.form["old"]):
            flash("Mot de passe actuel incorrect.", "danger")
        elif len(request.form["new"]) < 8:
            flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", "warning")
        else:
            current_user.set_password(request.form["new"])
            db.session.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("main.home"))
    return render_template("password.html")
