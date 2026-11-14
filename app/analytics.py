"""Couche analytique du Système d'Information Décisionnel (SID).

Fonctions de calcul réutilisées par les tableaux de bord : agrégations, moyennes,
scoring de risque (Early Warning System) et modèles de prévision simples
(régression linéaire, méthode des cohortes, taux de recouvrement historique).
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from flask_login import current_user

from . import db
from .models import (AcademicYear, Absence, Fee, Grade, Message, Student, Subject)

MONTHS_FR = ["Janv", "Févr", "Mars", "Avr", "Mai", "Juin", "Juil", "Août", "Sept", "Oct", "Nov", "Déc"]


def sid():
    return current_user.school_id


def years():
    return AcademicYear.query.filter_by(school_id=sid()).order_by(AcademicYear.start_year).all()


def year_bounds(y: AcademicYear):
    return date(y.start_year, 9, 15), date(y.start_year + 1, 6, 30)


def school_days(start: date, end: date) -> int:
    """Nombre de jours ouvrés (lundi-vendredi) entre deux dates incluses."""
    if end < start:
        return 0
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def linreg(xs, ys):
    """Régression linéaire par moindres carrés : y = a·x + b."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs) or 1
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return a, my - a * mx


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if not sx or not sy:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def student_avgs(year_id, term):
    """{student_id: moyenne pondérée} pour un trimestre donné."""
    rows = (db.session.query(Grade.student_id, Grade.score, Subject.coefficient)
            .join(Subject, Subject.id == Grade.subject_id)
            .filter(Grade.school_id == sid(), Grade.year_id == year_id, Grade.term == term).all())
    num, den = defaultdict(float), defaultdict(float)
    for s, score, coef in rows:
        num[s] += score * coef
        den[s] += coef
    return {s: num[s] / den[s] for s in num if den[s]}


def latest_term(year_id):
    t = (db.session.query(db.func.max(Grade.term))
         .filter(Grade.school_id == sid(), Grade.year_id == year_id).scalar())
    return t or 1


def previous_term(year, term):
    """(year_id, term) du trimestre précédent, éventuellement sur l'année précédente."""
    if term > 1:
        return year.id, term - 1
    prev = (AcademicYear.query.filter_by(school_id=sid(), start_year=year.start_year - 1).first())
    return (prev.id, 3) if prev else (None, None)


def active_students():
    return Student.query.filter_by(school_id=sid(), status="actif").all()


def absences_since(start, unjustified_only=False, kind="absence"):
    q = Absence.query.filter(Absence.school_id == sid(), Absence.on_date >= start, Absence.kind == kind)
    if unjustified_only:
        q = q.filter(Absence.justified.is_(False))
    return q


def late_fees_by_student():
    today = date.today()
    out = defaultdict(lambda: {"count": 0, "amount": 0.0, "oldest": None})
    q = (Fee.query.join(Student, Student.id == Fee.student_id)
         .filter(Fee.school_id == sid(), Fee.due_date < today, Fee.paid_amount < Fee.amount,
                 Student.status == "actif"))
    for f in q:
        d = out[f.student_id]
        d["count"] += 1
        d["amount"] += f.amount - (f.paid_amount or 0)
        d["oldest"] = min(d["oldest"], f.due_date) if d["oldest"] else f.due_date
    return out


def parent_read_rates():
    """{parent_id: (reçus, lus)} sur les 120 derniers jours."""
    since = date.today() - timedelta(days=120)
    out = defaultdict(lambda: [0, 0])
    q = (db.session.query(Message.recipient_id, Message.read_at)
         .filter(Message.school_id == sid(), Message.sent_at >= since))
    for rid, read_at in q:
        out[rid][0] += 1
        if read_at:
            out[rid][1] += 1
    return out


def compute_risks(year):
    """Early Warning System : score de risque 0-100 par élève actif.

    Le score combine des signaux académiques, d'assiduité, financiers et
    d'engagement parental. Chaque facteur est explicable (liste `factors`).
    """
    term = latest_term(year.id)
    cur = student_avgs(year.id, term)
    pyid, pterm = previous_term(year, term)
    prev = student_avgs(pyid, pterm) if pyid else {}
    start, _ = year_bounds(year)
    unj = Counter(a.student_id for a in absences_since(start, unjustified_only=True))
    tot_abs = Counter(a.student_id for a in absences_since(start))
    late = late_fees_by_student()
    reads = parent_read_rates()

    results = []
    for s in active_students():
        score, factors = 0, []
        avg = cur.get(s.id)
        delta = (avg - prev[s.id]) if (avg is not None and s.id in prev) else None
        if avg is not None and avg < 8:
            score += 40; factors.append(f"Moyenne très faible ({avg:.1f})")
        elif avg is not None and avg < 10:
            score += 25; factors.append(f"Moyenne insuffisante ({avg:.1f})")
        if delta is not None and delta <= -2:
            score += 20; factors.append(f"Baisse de {abs(delta):.1f} pts")
        if unj[s.id] >= 8:
            score += 30; factors.append(f"{unj[s.id]} absences non justifiées")
        elif unj[s.id] >= 4:
            score += 18; factors.append(f"{unj[s.id]} absences non justifiées")
        lf = late.get(s.id)
        if lf and lf["count"] >= 2:
            score += 12; factors.append(f"{lf['count']} mensualités impayées")
        rec, rd = reads.get(s.parent_id, (0, 0))
        parent_disengaged = rec >= 3 and rd / rec < 0.25
        if parent_disengaged:
            score += 10; factors.append("Parent ne consulte pas les messages")
        dropout = (tot_abs[s.id] >= 6 and avg is not None and avg < 9.5
                   and (bool(lf and lf["count"] >= 2) or parent_disengaged
                        or (delta is not None and delta <= -2.5)))
        double_signal = unj[s.id] >= 4 and delta is not None and delta <= -1.5
        results.append({
            "student": s, "score": min(score, 100), "avg": avg, "delta": delta,
            "absences": tot_abs[s.id], "unjustified": unj[s.id], "late": lf,
            "parent_disengaged": parent_disengaged, "dropout": dropout,
            "double_signal": double_signal, "factors": factors,
            "level": "Élevé" if score >= 60 else "Moyen" if score >= 35 else "Faible",
        })
    results.sort(key=lambda r: -r["score"])
    return results


def recommended_action(r):
    if r["dropout"]:
        return "Entretien famille + cellule d'écoute (risque d'abandon)"
    if r["double_signal"]:
        return "Convocation parent : assiduité et résultats"
    if r["avg"] is not None and r["avg"] < 10:
        return "Inscription au soutien scolaire"
    if r["unjustified"] >= 4:
        return "Suivi de l'assiduité par le surveillant"
    if r["late"]:
        return "Relance comptable / échéancier aménagé"
    return "Suivi standard"
