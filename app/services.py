"""Logique métier partagée (calcul des moyennes, bulletins)."""
from collections import defaultdict

from sqlalchemy import func

from . import db
from .models import Grade, Student, Subject


def class_term_averages(classroom_id, year_id, term):
    """Moyenne générale pondérée par coefficient pour chaque élève d'une classe."""
    rows = (db.session.query(Grade.student_id, Grade.score, Subject.coefficient)
            .join(Subject, Subject.id == Grade.subject_id)
            .filter(Grade.classroom_id == classroom_id, Grade.year_id == year_id, Grade.term == term)
            .all())
    num, den = defaultdict(float), defaultdict(float)
    for sid, score, coef in rows:
        num[sid] += score * coef
        den[sid] += coef
    return {sid: round(num[sid] / den[sid], 2) for sid in num if den[sid]}


def bulletin(student: Student, year_id, term):
    grades = (Grade.query.filter_by(student_id=student.id, year_id=year_id, term=term)
              .join(Subject).order_by(Subject.coefficient.desc()).all())
    class_avgs = class_term_averages(student.classroom_id, year_id, term)
    ranking = sorted(class_avgs.items(), key=lambda kv: -kv[1])
    rank = next((i + 1 for i, (sid, _) in enumerate(ranking) if sid == student.id), None)
    subj_class_avg = dict(
        db.session.query(Grade.subject_id, func.avg(Grade.score))
        .filter(Grade.classroom_id == student.classroom_id, Grade.year_id == year_id, Grade.term == term)
        .group_by(Grade.subject_id).all())
    lines = [{
        "subject": g.subject.name, "coef": g.subject.coefficient, "score": g.score,
        "class_avg": round(subj_class_avg.get(g.subject_id, 0), 2),
    } for g in grades]
    return {
        "lines": lines,
        "average": class_avgs.get(student.id),
        "rank": rank,
        "class_size": len(class_avgs),
        "class_average": round(sum(class_avgs.values()) / len(class_avgs), 2) if class_avgs else None,
    }
