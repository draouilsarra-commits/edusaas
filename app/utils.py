"""Outils transverses : isolation multi-école et contrôle d'accès par rôle."""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from . import db
from .models import AcademicYear


def roles_required(*roles):
    """Décorateur RBAC : la route n'est accessible qu'aux rôles listés."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def scoped(model):
    """Requête filtrée sur l'école de l'utilisateur : cœur de l'isolation tenant."""
    return model.query.filter_by(school_id=current_user.school_id)


def get_scoped_or_404(model, obj_id):
    obj = db.session.get(model, obj_id)
    if obj is None or obj.school_id != current_user.school_id:
        abort(404)
    return obj


def current_year(school_id=None):
    sid = school_id or current_user.school_id
    return AcademicYear.query.filter_by(school_id=sid, is_current=True).first()


def mention(avg):
    if avg is None:
        return "-"
    if avg >= 16:
        return "Très bien"
    if avg >= 14:
        return "Bien"
    if avg >= 12:
        return "Assez bien"
    if avg >= 10:
        return "Passable"
    return "Insuffisant"
