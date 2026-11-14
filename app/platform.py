"""Console du super-administrateur SaaS : gestion des établissements (tenants)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func

from . import db
from .models import AcademicYear, Level, School, Student, Subject, User
from .utils import roles_required

bp = Blueprint("platform", __name__, url_prefix="/plateforme")

DEFAULT_LEVELS = ["7ème année", "8ème année", "9ème année",
                  "1ère secondaire", "2ème secondaire", "3ème secondaire"]
DEFAULT_SUBJECTS = [("Mathématiques", 3, 5), ("Français", 2, 4), ("Arabe", 2, 4),
                    ("Anglais", 1.5, 3), ("Sciences", 2, 3), ("Physique", 2, 2),
                    ("Histoire-Géographie", 1, 2), ("Informatique", 1, 1)]


def bootstrap_school(school, start_year):
    """Crée la structure de base d'un nouvel établissement."""
    db.session.add(AcademicYear(school_id=school.id, label=f"{start_year}-{start_year + 1}",
                                start_year=start_year, is_current=True))
    for i, name in enumerate(DEFAULT_LEVELS):
        db.session.add(Level(school_id=school.id, name=name, order=i, annual_fee=2800 + 200 * i))
    for name, coef, hours in DEFAULT_SUBJECTS:
        db.session.add(Subject(school_id=school.id, name=name, coefficient=coef, weekly_hours=hours))


@bp.route("/")
@roles_required("superadmin")
def schools():
    counts = dict(db.session.query(Student.school_id, func.count(Student.id))
                  .filter(Student.status == "actif").group_by(Student.school_id).all())
    users = dict(db.session.query(User.school_id, func.count(User.id)).group_by(User.school_id).all())
    return render_template("platform/schools.html", schools=School.query.order_by(School.id).all(),
                           counts=counts, users=users)


@bp.route("/nouvelle", methods=["POST"])
@roles_required("superadmin")
def new_school():
    f = request.form
    code = f["code"].strip().lower()
    if School.query.filter_by(code=code).first():
        flash("Ce code d'établissement existe déjà.", "danger")
        return redirect(url_for(".schools"))
    school = School(name=f["name"], code=code, city=f.get("city"), capacity=int(f.get("capacity") or 400))
    db.session.add(school)
    db.session.flush()
    from datetime import date
    today = date.today()
    bootstrap_school(school, today.year if today.month >= 9 else today.year - 1)
    admin = User(school_id=school.id, email=f["admin_email"].strip().lower(),
                 full_name=f["admin_name"], role="admin")
    admin.set_password(f["admin_password"])
    db.session.add(admin)
    db.session.commit()
    flash(f"Établissement « {school.name} » créé avec son administrateur.", "success")
    return redirect(url_for(".schools"))
