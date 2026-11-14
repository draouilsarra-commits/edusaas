"""Module Business Intelligence & Aide à la Décision : 10 tableaux de bord.

Chaque tableau de bord est une fonction qui renvoie une structure déclarative
(KPI, graphiques, tableaux, alertes) rendue par un gabarit générique. Ajouter un
tableau de bord = ajouter une fonction au registre `DASHBOARDS`.
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, timedelta

from flask import Blueprint, Response, abort, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from . import db
from .analytics import (MONTHS_FR, absences_since, active_students, compute_risks, latest_term,
                        late_fees_by_student, linreg, parent_read_rates, pearson, previous_term,
                        school_days, student_avgs, year_bounds, years)
from .models import (DAYS, Absence, Classroom, Complaint, Enrollment, Expense, Fee, Grade, Level,
                     Message, Payment, Room, School, Student, Subject, Teacher, TeacherAbsence,
                     TimetableSlot, User)
from .utils import current_year, roles_required, scoped

bp = Blueprint("bi", __name__, url_prefix="/bi")

PALETTE = ["#2a6fdb", "#16a37f", "#f59e0b", "#e5484d", "#8b5cf6", "#0ea5e9", "#64748b", "#d946ef"]
WEEK_SLOTS = 30  # 5 jours x 6 créneaux d'une heure


def chart(cid, title, ctype, labels, datasets, wide=False, **opts):
    for i, ds in enumerate(datasets):
        ds.setdefault("backgroundColor", PALETTE[i % len(PALETTE)])
        ds.setdefault("borderColor", PALETTE[i % len(PALETTE)])
    return {"id": cid, "title": title, "type": ctype, "labels": labels,
            "datasets": datasets, "wide": wide, "opts": opts}


def kpi(label, value, sub=None, tone=None):
    return {"label": label, "value": value, "sub": sub, "tone": tone}


def fmt_money(v):
    return f"{v:,.0f} DT".replace(",", " ")


def month_key(d):
    return (d.year, d.month)


def month_label(k):
    return f"{MONTHS_FR[k[1] - 1]} {str(k[0])[2:]}"


def add_months(k, n):
    y, m = divmod(k[0] * 12 + (k[1] - 1) + n, 12)
    return (y, m + 1)


# =========================================================================== 1. GÉNÉRAL
def dash_general():
    school = db.session.get(School, current_user.school_id)
    studs = active_students()
    by_class = Counter(s.classroom_id for s in studs)
    classes = scoped(Classroom).join(Level).order_by(Level.order, Classroom.name).all()
    by_level = defaultdict(int)
    for c in classes:
        by_level[c.level.name] += by_class.get(c.id, 0)
    total_cap = sum(c.capacity for c in classes)
    yrs = years()
    enr = dict(db.session.query(Enrollment.year_id, func.count(Enrollment.id))
               .filter_by(school_id=current_user.school_id).group_by(Enrollment.year_id).all())
    alerts, rows = [], []
    for c in classes:
        n, rate = by_class.get(c.id, 0), by_class.get(c.id, 0) / c.capacity * 100
        status = "Surchargée" if n > c.capacity else "Sous-remplie" if rate < 60 else "Normale"
        if status == "Surchargée":
            alerts.append(("danger", f"{c.name} est surchargée : {n} élèves pour {c.capacity} places."))
        elif status == "Sous-remplie":
            alerts.append(("warning", f"{c.name} est sous-remplie : {n}/{c.capacity} ({rate:.0f} %)."))
        rows.append([c.name, c.level.name, n, c.capacity, f"{rate:.0f} %", status])
    return {
        "kpis": [
            kpi("Élèves actifs", len(studs)),
            kpi("Enseignants", scoped(Teacher).count()),
            kpi("Classes", len(classes)),
            kpi("Taux de remplissage", f"{len(studs) / total_cap * 100:.0f} %" if total_cap else "-",
                f"{len(studs)} / {total_cap} places (capacité établissement : {school.capacity})"),
        ],
        "alerts": alerts,
        "charts": [
            chart("lvl", "Répartition des élèves par niveau", "bar", list(by_level),
                  [{"label": "Élèves", "data": list(by_level.values())}]),
            chart("enr", "Évolution des inscriptions par année scolaire", "line",
                  [y.label for y in yrs], [{"label": "Inscriptions", "data": [enr.get(y.id, 0) for y in yrs],
                                            "tension": .3}]),
            chart("cap", "Effectif actuel vs capacité d'accueil par classe", "bar", [c.name for c in classes],
                  [{"label": "Effectif", "data": [by_class.get(c.id, 0) for c in classes]},
                   {"label": "Capacité", "data": [c.capacity for c in classes], "type": "line",
                    "borderDash": [5, 4], "pointRadius": 0}], wide=True),
        ],
        "tables": [{"title": "État des classes", "head": ["Classe", "Niveau", "Effectif", "Capacité",
                                                          "Remplissage", "Statut"], "rows": rows}],
    }


# =========================================================================== 2. FINANCIER
def revenue_forecast(horizon=3):
    """Prévision des recettes : échéances futures x taux de recouvrement historique
    (même mois de l'année précédente), avec repli sur le taux global."""
    today = date.today()
    fees = Fee.query.filter_by(school_id=current_user.school_id).all()
    pays = Payment.query.filter_by(school_id=current_user.school_id).all()
    due_m, paid_m = defaultdict(float), defaultdict(float)
    for f in fees:
        due_m[month_key(f.due_date)] += f.amount
    for p in pays:
        paid_m[month_key(p.paid_on)] += p.amount
    past = [k for k in due_m if k < month_key(today)]
    global_rate = (sum(paid_m[k] for k in past) / sum(due_m[k] for k in past)) if past else 0.9
    out = []
    cur = month_key(today)
    for i in range(1, horizon + 1):
        k = add_months(cur, i)
        ly = (k[0] - 1, k[1])
        rate = paid_m[ly] / due_m[ly] if due_m.get(ly) else global_rate
        out.append((k, due_m.get(k, 0) * min(rate, 1.1)))
    return out, paid_m, due_m, global_rate


def dash_financier():
    year = current_year()
    today = date.today()
    fees = scoped(Fee).filter_by(year_id=year.id).all()
    expected = sum(f.amount for f in fees)
    paid = sum(f.paid_amount or 0 for f in fees)
    due_now = [f for f in fees if f.due_date <= today]
    due_amt = sum(f.amount for f in due_now)
    due_paid = sum(f.paid_amount or 0 for f in due_now)
    recovery = due_paid / due_amt * 100 if due_amt else 0

    forecast, paid_m, _, _ = revenue_forecast(3)
    start = add_months(month_key(today), -13)
    hist_keys = [add_months(start, i) for i in range(14)]
    labels = [month_label(k) for k in hist_keys] + [month_label(k) for k, _ in forecast]
    hist = [round(paid_m.get(k, 0)) for k in hist_keys] + [None] * len(forecast)
    fc = [None] * (len(hist_keys) - 1) + [round(paid_m.get(hist_keys[-1], 0))] + [round(v) for _, v in forecast]

    # recouvrement par niveau
    lvl_due, lvl_paid = defaultdict(float), defaultdict(float)
    for f in due_now:
        if f.student.classroom:
            n = f.student.classroom.level.name
            lvl_due[n] += f.amount
            lvl_paid[n] += f.paid_amount or 0
    levels = [l.name for l in scoped(Level).order_by(Level.order)]
    methods = Counter()
    for p in scoped(Payment).filter(Payment.paid_on >= date(year.start_year, 9, 1)):
        methods[p.method] += p.amount

    late = late_fees_by_student()
    studs = {s.id: s for s in scoped(Student).filter(Student.id.in_(list(late))).all()} if late else {}
    rows = []
    for st_id, d in sorted(late.items(), key=lambda kv: -kv[1]["amount"])[:20]:
        s = studs.get(st_id)
        if not s or s.status != "actif":
            continue
        rows.append([s.parent.full_name if s.parent else "-", s.full_name,
                     s.classroom.name if s.classroom else "-", d["count"], fmt_money(d["amount"]),
                     f"{(today - d['oldest']).days} j",
                     {"link": url_for("main.student_detail", sid=s.id), "text": "Voir"}])
    alerts = []
    if recovery < 85:
        alerts.append(("danger", f"Taux de recouvrement des échéances échues faible : {recovery:.1f} %."))
    if late:
        alerts.append(("warning", f"{len(late)} élèves ont au moins une mensualité en retard."))
    return {
        "kpis": [
            kpi("Frais attendus (année)", fmt_money(expected)),
            kpi("Montant encaissé", fmt_money(paid), f"{paid / expected * 100:.0f} % de l'annuel" if expected else ""),
            kpi("Reste à payer", fmt_money(expected - paid), f"dont {fmt_money(due_amt - due_paid)} échu",
                "warn"),
            kpi("Taux de recouvrement", f"{recovery:.1f} %", "sur les échéances échues",
                "good" if recovery >= 90 else "bad"),
        ],
        "alerts": alerts,
        "charts": [
            chart("rev", "Recettes mensuelles et prévision des 3 prochains mois", "line", labels,
                  [{"label": "Recettes encaissées", "data": hist, "tension": .3, "fill": False},
                   {"label": "Prévision", "data": fc, "borderDash": [6, 4], "tension": .3}], wide=True),
            chart("lvl", "Taux de recouvrement par niveau (%)", "bar", levels,
                  [{"label": "Recouvrement", "data": [round(lvl_paid[l] / lvl_due[l] * 100, 1) if lvl_due[l] else 0
                                                      for l in levels]}], ymax=100),
            chart("meth", "Encaissements par moyen de paiement", "doughnut", list(methods),
                  [{"label": "Montant", "data": [round(v) for v in methods.values()],
                    "backgroundColor": PALETTE[:len(methods)]}]),
        ],
        "tables": [{"title": "Parents ayant des retards de paiement", "head":
                    ["Parent", "Élève", "Classe", "Échéances en retard", "Montant dû", "Retard max", ""],
                    "rows": rows}],
        "note": "Prévision : montants des échéances à venir × taux de recouvrement observé le même mois "
                "de l'année précédente (repli sur le taux global si historique absent).",
    }


# =========================================================================== 3. PÉDAGOGIQUE
def dash_pedagogique():
    year = current_year()
    term = request.args.get("trimestre", type=int) or latest_term(year.id)
    avgs = student_avgs(year.id, term)
    studs = {s.id: s for s in active_students()}
    avgs = {k: v for k, v in avgs.items() if k in studs}
    n = len(avgs) or 1
    success = sum(1 for v in avgs.values() if v >= 10)
    struggling = [k for k, v in avgs.items() if v < 10]

    cls_vals, lvl_vals = defaultdict(list), defaultdict(list)
    for k, v in avgs.items():
        c = studs[k].classroom
        cls_vals[c.name].append(v)
        lvl_vals[(c.level.order, c.level.name)].append(v)
    cls_names = sorted(cls_vals)
    lvl_keys = sorted(lvl_vals)

    subj = (db.session.query(Subject.name, func.avg(Grade.score))
            .join(Grade, Grade.subject_id == Subject.id)
            .filter(Grade.school_id == current_user.school_id, Grade.year_id == year.id, Grade.term == term)
            .group_by(Subject.name).all())
    subj.sort(key=lambda x: -x[1])

    # évolution entre trimestres (année précédente + année en cours)
    evo_labels, evo_avg, evo_success = [], [], []
    for y in years()[-2:]:
        for t in (1, 2, 3):
            a = student_avgs(y.id, t)
            if not a:
                continue
            evo_labels.append(f"{y.label} T{t}")
            evo_avg.append(round(sum(a.values()) / len(a), 2))
            evo_success.append(round(sum(1 for v in a.values() if v >= 10) / len(a) * 100, 1))

    pyid, pterm = previous_term(year, term)
    prev = student_avgs(pyid, pterm) if pyid else {}
    weak = defaultdict(list)
    for g in (Grade.query.filter(Grade.school_id == current_user.school_id, Grade.year_id == year.id,
                                 Grade.term == term, Grade.score < 10).all()):
        weak[g.student_id].append(g.subject.name)
    need = []
    for k, v in avgs.items():
        d = v - prev[k] if k in prev else None
        if v < 10 or (d is not None and d <= -2):
            need.append((k, v, d))
    need.sort(key=lambda x: x[1])
    rows = [[studs[k].full_name, studs[k].classroom.name, f"{v:.2f}",
             (f"{d:+.2f}" if d is not None else "-"), ", ".join(weak[k][:4]) or "-",
             {"link": url_for("main.bulletin", sid=k, trimestre=term), "text": "Bulletin"}] for k, v, d in need[:30]]
    return {
        "filters": {"trimestre": term},
        "kpis": [
            kpi("Moyenne générale", f"{sum(avgs.values()) / n:.2f}/20", f"Trimestre {term} – {year.label}"),
            kpi("Taux de réussite", f"{success / n * 100:.1f} %", "moyenne ≥ 10", "good"),
            kpi("Taux d'échec", f"{(n - success) / n * 100:.1f} %", "moyenne < 10", "bad"),
            kpi("Élèves à accompagner", len(need), f"dont {len(struggling)} sous la moyenne", "warn"),
        ],
        "charts": [
            chart("cls", "Moyenne générale par classe", "bar", cls_names,
                  [{"label": "Moyenne", "data": [round(sum(cls_vals[c]) / len(cls_vals[c]), 2) for c in cls_names]}],
                  ymax=20),
            chart("lvl", "Moyenne générale par niveau", "bar", [k[1] for k in lvl_keys],
                  [{"label": "Moyenne", "data": [round(sum(lvl_vals[k]) / len(lvl_vals[k]), 2) for k in lvl_keys]}],
                  ymax=20),
            chart("subj", "Classement des matières selon les performances", "bar", [s[0] for s in subj],
                  [{"label": "Moyenne", "data": [round(s[1], 2) for s in subj]}], horizontal=True, ymax=20),
            chart("evo", "Évolution des résultats entre les trimestres", "line", evo_labels,
                  [{"label": "Moyenne générale", "data": evo_avg, "yAxisID": "y", "tension": .3},
                   {"label": "Taux de réussite (%)", "data": evo_success, "yAxisID": "y1", "tension": .3}],
                  dual=True),
        ],
        "tables": [{"title": "Élèves nécessitant un accompagnement pédagogique",
                    "head": ["Élève", "Classe", "Moyenne", "Évolution", "Matières faibles", ""], "rows": rows}],
    }


# =========================================================================== 4. ABSENCES
def dash_absences():
    year = current_year()
    start, _ = year_bounds(year)
    today = date.today()
    studs = {s.id: s for s in active_students()}
    abs_all = [a for a in absences_since(start) if a.student_id in studs]
    lates = absences_since(start, kind="retard").count()
    days = school_days(start, today) or 1
    rate = len(abs_all) / (len(studs) * days) * 100 if studs else 0
    justified = sum(1 for a in abs_all if a.justified)

    cls_size = Counter(s.classroom.name for s in studs.values())
    cls_abs = Counter(studs[a.student_id].classroom.name for a in abs_all)
    cls_names = sorted(cls_size)

    # 12 derniers mois (inclut l'année précédente)
    mstart = add_months(month_key(today), -11)
    keys = [add_months(mstart, i) for i in range(12)]
    since = date(*keys[0], 1)
    mj, mu = Counter(), Counter()
    for a in Absence.query.filter(Absence.school_id == current_user.school_id, Absence.kind == "absence",
                                  Absence.on_date >= since):
        (mj if a.justified else mu)[month_key(a.on_date)] += 1

    # relation absentéisme / résultats
    term = latest_term(year.id)
    avgs = student_avgs(year.id, term)
    per_student = Counter(a.student_id for a in abs_all)
    pts = [{"x": per_student.get(k, 0), "y": round(v, 2)} for k, v in avgs.items() if k in studs]
    r = pearson([p["x"] for p in pts], [p["y"] for p in pts])

    unj = Counter(a.student_id for a in abs_all if not a.justified)
    rep = sorted([k for k, v in unj.items() if v >= 4], key=lambda k: -unj[k])
    rows = [[studs[k].full_name, studs[k].classroom.name, per_student[k], unj[k],
             f"{per_student[k] / days * 100:.1f} %", f"{avgs[k]:.2f}" if k in avgs else "-",
             {"link": url_for("main.student_detail", sid=k), "text": "Voir"}] for k in rep[:30]]
    alerts = [("warning", f"{len(rep)} élèves cumulent au moins 4 absences non justifiées.")] if rep else []
    if r < -0.3:
        alerts.append(("info", f"Corrélation négative marquée entre absences et moyenne (r = {r:.2f}) : "
                               "l'assiduité est un levier de réussite."))
    return {
        "kpis": [
            kpi("Taux d'absentéisme", f"{rate:.2f} %", f"{len(abs_all)} absences sur {days} jours ouvrés"),
            kpi("Absences justifiées", justified, f"{justified / len(abs_all) * 100:.0f} %" if abs_all else ""),
            kpi("Absences non justifiées", len(abs_all) - justified, None, "bad"),
            kpi("Retards", lates),
            kpi("Corrélation absences / moyenne", f"{r:.2f}", "coefficient de Pearson"),
        ],
        "alerts": alerts,
        "charts": [
            chart("month", "Évolution mensuelle des absences", "bar", [month_label(k) for k in keys],
                  [{"label": "Justifiées", "data": [mj[k] for k in keys]},
                   {"label": "Non justifiées", "data": [mu[k] for k in keys], "backgroundColor": PALETTE[3]}],
                  stacked=True, wide=True),
            chart("cls", "Taux d'absentéisme par classe (%)", "bar", cls_names,
                  [{"label": "Taux", "data": [round(cls_abs[c] / (cls_size[c] * days) * 100, 2) for c in cls_names]}]),
            chart("scatter", "Relation absentéisme / résultats (1 point = 1 élève)", "scatter", [],
                  [{"label": "Élèves", "data": pts, "pointRadius": 3}], xlabel="Nombre d'absences",
                  ylabel="Moyenne T" + str(term)),
        ],
        "tables": [{"title": "Élèves ayant des absences répétitives",
                    "head": ["Élève", "Classe", "Absences", "Non justifiées", "Taux", "Moyenne", ""], "rows": rows}],
    }


# =========================================================================== 5. ENSEIGNANTS
def teacher_loads():
    return dict(db.session.query(TimetableSlot.teacher_id, func.count(TimetableSlot.id))
                .filter_by(school_id=current_user.school_id).group_by(TimetableSlot.teacher_id).all())


def dash_enseignants():
    year = current_year()
    teachers = scoped(Teacher).all()
    load = teacher_loads()
    by_subj = Counter(t.subject.name for t in teachers)
    hours_subj = Counter()
    for s in scoped(TimetableSlot).all():
        hours_subj[s.subject.name] += 1
    lvl_t = defaultdict(set)
    for cid, tid in db.session.query(TimetableSlot.classroom_id, TimetableSlot.teacher_id).filter_by(
            school_id=current_user.school_id):
        lvl_t[db.session.get(Classroom, cid).level.name].add(tid)
    levels = [l.name for l in scoped(Level).order_by(Level.order)]
    tabs = scoped(TeacherAbsence).filter(TeacherAbsence.on_date >= date(year.start_year, 9, 1)).all()
    replaced = sum(1 for a in tabs if a.replacement_id)
    rows_load = []
    over = under = 0
    for t in sorted(teachers, key=lambda t: -load.get(t.id, 0)):
        h = load.get(t.id, 0)
        st = "Surcharge" if h > t.contract_hours else "Sous-charge" if h < 0.7 * t.contract_hours else "Normale"
        over += st == "Surcharge"; under += st == "Sous-charge"
        if st != "Normale":
            rows_load.append([t.name, t.subject.name, h, t.contract_hours, f"{h - t.contract_hours:+d} h", st])
    rows_abs = [[a.on_date.strftime("%d/%m/%Y"), a.teacher.name, a.hours,
                 a.replacement.name if a.replacement else "Non remplacé"]
                for a in sorted(tabs, key=lambda a: a.on_date, reverse=True)[:15]]
    ts = sorted(teachers, key=lambda t: t.name)
    total_h = sum(load.values())
    return {
        "kpis": [
            kpi("Enseignants", len(teachers)),
            kpi("Heures d'enseignement / semaine", total_h),
            kpi("Charge moyenne", f"{total_h / len(teachers):.1f} h" if teachers else "-"),
            kpi("Surcharge / sous-charge", f"{over} / {under}", "enseignants", "warn" if over or under else None),
            kpi("Absences enseignants", len(tabs), f"{replaced / len(tabs) * 100:.0f} % remplacées" if tabs else ""),
        ],
        "charts": [
            chart("subj", "Nombre d'enseignants par matière", "bar", list(by_subj),
                  [{"label": "Enseignants", "data": list(by_subj.values())}]),
            chart("lvl", "Nombre d'enseignants intervenant par niveau", "bar", levels,
                  [{"label": "Enseignants", "data": [len(lvl_t[l]) for l in levels]}]),
            chart("load", "Charge horaire de chaque enseignant vs contrat", "bar", [t.name for t in ts],
                  [{"label": "Heures planifiées", "data": [load.get(t.id, 0) for t in ts]},
                   {"label": "Contrat", "data": [t.contract_hours for t in ts], "type": "line",
                    "borderDash": [5, 4], "pointRadius": 2}], wide=True),
            chart("hours", "Répartition des heures d'enseignement par matière", "doughnut", list(hours_subj),
                  [{"label": "Heures", "data": list(hours_subj.values()),
                    "backgroundColor": PALETTE[:len(hours_subj)]}]),
        ],
        "tables": [
            {"title": "Enseignants en surcharge ou en sous-charge",
             "head": ["Enseignant", "Matière", "Heures", "Contrat", "Écart", "Statut"], "rows": rows_load},
            {"title": "Suivi des absences et des remplacements",
             "head": ["Date", "Enseignant", "Heures", "Remplaçant"], "rows": rows_abs},
        ],
    }


# =========================================================================== 6. INSCRIPTIONS
def enrollment_stats():
    yrs = years()
    stats = []
    for y in yrs:
        q = Enrollment.query.filter_by(school_id=current_user.school_id, year_id=y.id)
        rows = q.all()
        stats.append({
            "year": y, "total": len(rows),
            "new": sum(1 for e in rows if e.kind == "nouvelle"),
            "re": sum(1 for e in rows if e.kind == "reinscription"),
            "left": sum(1 for e in rows if e.outcome == "parti"),
            "dropout": sum(1 for e in rows if e.outcome == "abandon"),
        })
    return stats


def forecast_enrollment(stats):
    """Deux méthodes : tendance linéaire et méthode des cohortes (renouvellement)."""
    xs = list(range(len(stats)))
    a, b = linreg(xs, [s["total"] for s in stats])
    trend = round(a * len(stats) + b)
    final_level = scoped(Level).order_by(Level.order.desc()).first()
    eligible = scoped(Student).filter_by(status="actif").join(Classroom).filter(
        Classroom.level_id != final_level.id).count()
    renew = [s["re"] / p["total"] for p, s in zip(stats, stats[1:]) if p["total"]]
    rate = sum(renew[-2:]) / len(renew[-2:]) if renew else 0.85
    rate = min(rate / 0.84, 0.97)  # les sortants du dernier niveau sont exclus de `eligible`
    new_avg = sum(s["new"] for s in stats[-3:]) / len(stats[-3:])
    cohort = round(eligible * rate + new_avg)
    return trend, cohort, rate


def dash_inscriptions():
    stats = enrollment_stats()
    cur, prev = stats[-1], (stats[-2] if len(stats) > 1 else None)
    renewal = cur["re"] / prev["total"] * 100 if prev and prev["total"] else 0
    trend, cohort, _ = forecast_enrollment(stats)
    cities = Counter(s.city or "Inconnue" for s in active_students()).most_common(8)
    labels = [s["year"].label for s in stats]
    next_label = f"{stats[-1]['year'].start_year + 1}-{stats[-1]['year'].start_year + 2}"
    alerts = []
    if prev and cur["new"] < prev["new"]:
        alerts.append(("warning", f"Baisse des nouvelles inscriptions : {cur['new']} contre {prev['new']} "
                                  "l'an dernier."))
    return {
        "kpis": [
            kpi("Nouvelles inscriptions", cur["new"], cur["year"].label),
            kpi("Réinscriptions", cur["re"]),
            kpi("Taux de renouvellement", f"{renewal:.1f} %", "réinscrits / effectif N-1"),
            kpi("Départs et abandons", cur["left"] + cur["dropout"],
                f"{cur['left']} départs, {cur['dropout']} abandons", "bad"),
            kpi("Prévision " + next_label, cohort, f"méthode des cohortes (tendance : {trend})", "good"),
        ],
        "alerts": alerts,
        "charts": [
            chart("kinds", "Nouvelles inscriptions et réinscriptions par année", "bar", labels,
                  [{"label": "Nouvelles", "data": [s["new"] for s in stats]},
                   {"label": "Réinscriptions", "data": [s["re"] for s in stats]}], stacked=True),
            chart("leave", "Départs et abandons par année", "bar", labels,
                  [{"label": "Départs", "data": [s["left"] for s in stats], "backgroundColor": PALETTE[2]},
                   {"label": "Abandons", "data": [s["dropout"] for s in stats], "backgroundColor": PALETTE[3]}]),
            chart("geo", "Répartition des élèves par origine géographique", "doughnut", [c[0] for c in cities],
                  [{"label": "Élèves", "data": [c[1] for c in cities], "backgroundColor": PALETTE[:len(cities)]}]),
            chart("fc", "Prévision de l'effectif pour la prochaine année scolaire", "line", labels + [next_label],
                  [{"label": "Effectif réel", "data": [s["total"] for s in stats] + [None], "tension": .3},
                   {"label": "Prévision (cohortes)", "data": [None] * (len(stats) - 1) + [stats[-1]["total"], cohort],
                    "borderDash": [6, 4]}]),
        ],
        "tables": [{"title": "Synthèse par année scolaire",
                    "head": ["Année", "Effectif", "Nouvelles", "Réinscriptions", "Départs", "Abandons"],
                    "rows": [[s["year"].label, s["total"], s["new"], s["re"], s["left"], s["dropout"]]
                             for s in stats]}],
        "note": "Méthode des cohortes : élèves actifs hors dernier niveau × taux de réinscription moyen "
                "des deux dernières années + moyenne des nouvelles inscriptions sur 3 ans.",
    }


# =========================================================================== 7. COMMUNICATIONS
def dash_communications():
    year = current_year()
    start = date(year.start_year, 9, 1)
    msgs = (db.session.query(Message.sent_at, Message.read_at, User.role)
            .join(User, User.id == Message.recipient_id)
            .filter(Message.school_id == current_user.school_id, Message.sent_at >= start - timedelta(days=300))
            .all())
    to_parents = [m for m in msgs if m.role == "parent"]
    this_year = [m for m in to_parents if m.sent_at.date() >= start]
    read = sum(1 for m in this_year if m.read_at)
    reads = parent_read_rates()
    non_readers = [pid for pid, (rec, rd) in reads.items() if rec >= 3 and rd == 0]
    parents = {u.id: u for u in scoped(User).filter_by(role="parent").all()}
    non_readers = [p for p in non_readers if p in parents]

    mstart = add_months(month_key(date.today()), -11)
    keys = [add_months(mstart, i) for i in range(12)]
    sent_m, read_m = Counter(), Counter()
    for m in to_parents:
        sent_m[month_key(m.sent_at)] += 1
        if m.read_at:
            read_m[month_key(m.sent_at)] += 1

    comps = scoped(Complaint).all()
    cats = sorted({c.category for c in comps})
    statuses = ["ouverte", "en cours", "résolue"]
    res_days = [(c.resolved_at - c.created_at).days for c in comps if c.resolved_at]
    open_c = [c for c in comps if c.status != "résolue"]

    risks = {r["student"].id: r for r in compute_risks(year)}
    rows = []
    for s in active_students():
        r = risks.get(s.id)
        rec, rd = reads.get(s.parent_id, (0, 0))
        if r and rec >= 3 and rd / rec < 0.25 and (r["score"] >= 35):
            rows.append([s.parent.full_name if s.parent else "-", s.full_name, s.classroom.name,
                         f"{rd}/{rec}", r["level"], "; ".join(r["factors"][:3])])
    rows.sort(key=lambda x: x[4] != "Élevé")
    alerts = []
    if non_readers:
        alerts.append(("warning", f"{len(non_readers)} parents n'ont lu aucun des messages reçus récemment : "
                                  "privilégier un contact téléphonique ou SMS."))
    if rows:
        alerts.append(("danger", f"{len(rows)} élèves à risque ont un parent peu joignable : suivi parental "
                                 "à renforcer."))
    return {
        "kpis": [
            kpi("Messages envoyés aux parents", len(this_year), year.label),
            kpi("Taux de lecture", f"{read / len(this_year) * 100:.1f} %" if this_year else "-", None,
                "good" if this_year and read / len(this_year) > .7 else "warn"),
            kpi("Parents ne consultant pas", len(non_readers), "0 message lu (≥ 3 reçus, 120 j)", "bad"),
            kpi("Réclamations en cours", len(open_c), f"{len(comps)} au total"),
            kpi("Délai moyen de résolution", f"{sum(res_days) / len(res_days):.1f} j" if res_days else "-"),
        ],
        "alerts": alerts,
        "charts": [
            chart("msg", "Messages envoyés et lus par mois", "line", [month_label(k) for k in keys],
                  [{"label": "Envoyés", "data": [sent_m[k] for k in keys], "tension": .3},
                   {"label": "Lus", "data": [read_m[k] for k in keys], "tension": .3}], wide=True),
            chart("comp", "Réclamations et demandes par catégorie", "bar", cats,
                  [{"label": st.capitalize(), "data": [sum(1 for c in comps if c.category == cat and c.status == st)
                                                       for cat in cats]} for st in statuses], stacked=True),
        ],
        "tables": [{"title": "Situations nécessitant un renforcement du suivi parental",
                    "head": ["Parent", "Élève", "Classe", "Messages lus", "Risque", "Facteurs"], "rows": rows[:25]},
                   {"title": "Parents qui ne consultent pas les messages",
                    "head": ["Parent", "Téléphone", "Messages reçus (120 j)"],
                    "rows": [[parents[p].full_name, parents[p].phone or "-", reads[p][0]] for p in non_readers[:25]]}],
    }


# =========================================================================== 8. EMPLOIS DU TEMPS
def detect_conflicts():
    slots = scoped(TimetableSlot).all()
    by_t, by_r, by_c = defaultdict(list), defaultdict(list), defaultdict(list)
    for s in slots:
        by_t[(s.teacher_id, s.day, s.hour)].append(s)
        by_r[(s.room_id, s.day, s.hour)].append(s)
        by_c[(s.classroom_id, s.day, s.hour)].append(s)
    out = []
    for k, v in by_t.items():
        if len(v) > 1:
            out.append(["Enseignant", v[0].teacher.name, f"{DAYS[k[1]]} {k[2]}h", ", ".join(x.classroom.name for x in v)])
    for k, v in by_r.items():
        if len(v) > 1:
            out.append(["Salle", v[0].room.name, f"{DAYS[k[1]]} {k[2]}h", ", ".join(x.classroom.name for x in v)])
    for k, v in by_c.items():
        if len(v) > 1:
            out.append(["Classe", v[0].classroom.name, f"{DAYS[k[1]]} {k[2]}h", ", ".join(x.subject.name for x in v)])
    return out, slots


def dash_ressources():
    conflicts, slots = detect_conflicts()
    rooms = scoped(Room).order_by(Room.name).all()
    occ = Counter(s.room_id for s in slots)
    rates = {r.id: occ[r.id] / WEEK_SLOTS * 100 for r in rooms}
    under = [r for r in rooms if rates[r.id] < 40]
    from .main import HOURS
    grid_labels = [f"{DAYS[d][:3]} {h}h" for d in range(5) for h in HOURS]
    used = Counter((s.day, s.hour) for s in slots)
    grid = [round(len({s.room_id for s in slots if s.day == d and s.hour == h}) / len(rooms) * 100, 1) if rooms else 0
            for d in range(5) for h in HOURS]
    load = teacher_loads()
    ts = sorted(scoped(Teacher).all(), key=lambda t: -load.get(t.id, 0))
    balance = []
    for c in scoped(Classroom).order_by(Classroom.name):
        per_day = Counter(s.day for s in slots if s.classroom_id == c.id)
        vals = [per_day.get(d, 0) for d in range(5)]
        spread = max(vals) - min(vals)
        balance.append([c.name, sum(vals), " / ".join(map(str, vals)), spread,
                        "Déséquilibré" if spread >= 3 else "Équilibré"])
    alerts = [("danger", f"{len(conflicts)} conflit(s) d'horaires détecté(s).")] if conflicts else []
    if under:
        alerts.append(("info", "Salles sous-utilisées (< 40 %) : " + ", ".join(r.name for r in under)
                       + " – envisager une réaffectation ou une mutualisation."))
    avg_rate = sum(rates.values()) / len(rates) if rates else 0
    return {
        "kpis": [
            kpi("Taux d'occupation moyen des salles", f"{avg_rate:.0f} %", f"base {WEEK_SLOTS} créneaux / semaine"),
            kpi("Conflits d'horaires", len(conflicts), None, "bad" if conflicts else "good"),
            kpi("Salles sous-utilisées", len(under), "< 40 % d'occupation", "warn" if under else None),
            kpi("Créneaux planifiés", len(slots), "heures de cours / semaine"),
            kpi("Créneau le plus chargé", (lambda k: f"{DAYS[k[0]]} {k[1]}h")(used.most_common(1)[0][0]) if used else "-"),
        ],
        "alerts": alerts,
        "charts": [
            chart("rooms", "Taux d'occupation des salles (%)", "bar", [r.name for r in rooms],
                  [{"label": "Occupation", "data": [round(rates[r.id], 1) for r in rooms]}], ymax=100),
            chart("grid", "Taux d'occupation des salles par créneau (%)", "bar", grid_labels,
                  [{"label": "Salles occupées", "data": grid}], wide=True, ymax=100),
            chart("teach", "Répartition des heures par enseignant", "bar", [t.name for t in ts],
                  [{"label": "Heures / semaine", "data": [load.get(t.id, 0) for t in ts]}], wide=True),
        ],
        "tables": [
            {"title": "Conflits d'horaires", "head": ["Type", "Ressource", "Créneau", "En conflit"], "rows": conflicts},
            {"title": "Équilibre des emplois du temps par classe",
             "head": ["Classe", "Heures / sem.", "Lun / Mar / Mer / Jeu / Ven", "Écart max", "Diagnostic"],
             "rows": balance},
        ],
    }


# =========================================================================== 9. RISQUES (EWS)
def dash_risques():
    year = current_year()
    risks = compute_risks(year)
    from .analytics import recommended_action
    high = [r for r in risks if r["level"] == "Élevé"]
    medium = [r for r in risks if r["level"] == "Moyen"]
    dropout = [r for r in risks if r["dropout"]]
    double = [r for r in risks if r["double_signal"]]
    late = late_fees_by_student()
    # risque d'impayés : retards multiples ou historique de paiements tardifs
    hist_late = defaultdict(lambda: [0, 0])
    due_by_fee = dict(db.session.query(Fee.id, Fee.due_date).filter_by(school_id=current_user.school_id))
    for st_id, fee_id, paid_on in db.session.query(Payment.student_id, Payment.fee_id, Payment.paid_on).filter_by(
            school_id=current_user.school_id):
        if fee_id in due_by_fee:
            hist_late[st_id][0] += 1
            hist_late[st_id][1] += (paid_on - due_by_fee[fee_id]).days > 30
    studs = {s.id: s for s in active_students()}
    pay_risk = []
    for k, s in studs.items():
        n, l = hist_late.get(k, (0, 0))
        ratio = l / n if n else 0
        cur = late.get(k)
        if (cur and cur["count"] >= 3) or (cur and cur["count"] >= 2 and ratio > .5) or (ratio > .6 and n >= 6):
            pay_risk.append([s.parent.full_name if s.parent else "-", s.full_name,
                             cur["count"] if cur else 0, fmt_money(cur["amount"]) if cur else "0 DT",
                             f"{ratio * 100:.0f} %", "Échéancier aménagé + relance" if cur else "Relance préventive"])
    pay_risk.sort(key=lambda r: -r[2])

    def row(r):
        s = r["student"]
        return [s.full_name, s.classroom.name, r["score"], r["level"], "; ".join(r["factors"]),
                recommended_action(r), {"link": url_for("main.student_detail", sid=s.id), "text": "Dossier"}]
    alerts = []
    for r in dropout[:5]:
        alerts.append(("danger", f"Risque d'abandon : {r['student'].full_name} ({r['student'].classroom.name}) – "
                                 + "; ".join(r["factors"][:3])))
    for r in double[:5]:
        if not r["dropout"]:
            alerts.append(("warning", f"Absences répétées + baisse des résultats : {r['student'].full_name} "
                                      f"({r['student'].classroom.name})."))
    cls_risk = Counter(r["student"].classroom.name for r in high + medium)
    names = sorted(cls_risk)
    return {
        "kpis": [
            kpi("Risque d'échec élevé", len(high), f"+ {len(medium)} à risque moyen", "bad"),
            kpi("Risque d'abandon", len(dropout), "absences + résultats + signaux familiaux", "bad"),
            kpi("Absences + baisse des résultats", len(double), "signal combiné", "warn"),
            kpi("Risque d'impayés", len(pay_risk), "élèves (retards en cours ou historique)", "warn"),
        ],
        "alerts": alerts,
        "charts": [
            chart("lvl", "Répartition des élèves par niveau de risque", "doughnut", ["Élevé", "Moyen", "Faible"],
                  [{"label": "Élèves", "data": [len(high), len(medium), len(risks) - len(high) - len(medium)],
                    "backgroundColor": [PALETTE[3], PALETTE[2], PALETTE[1]]}]),
            chart("cls", "Élèves à risque (élevé + moyen) par classe", "bar", names,
                  [{"label": "Élèves", "data": [cls_risk[n] for n in names], "backgroundColor": PALETTE[3]}]),
        ],
        "tables": [
            {"title": "Élèves à risque d'échec scolaire – intervention précoce",
             "head": ["Élève", "Classe", "Score", "Niveau", "Facteurs", "Action recommandée", ""],
             "rows": [row(r) for r in (high + medium)[:40]]},
            {"title": "Familles à risque d'impayés",
             "head": ["Parent", "Élève", "Retards en cours", "Montant dû", "Historique paiements tardifs",
                      "Action"], "rows": pay_risk[:25]},
        ],
        "note": "Score de risque (0-100) : moyenne < 10 (25 pts) ou < 8 (40), baisse ≥ 2 pts (20), absences "
                "non justifiées ≥ 4 (18) ou ≥ 8 (30), ≥ 2 mensualités impayées (12), parent non lecteur (10). "
                "Élevé ≥ 60, moyen ≥ 35.",
    }


# =========================================================================== 10. STRATÉGIQUE
def dash_strategique():
    year = current_year()
    stats = enrollment_stats()
    trend, cohort, renew_rate = forecast_enrollment(stats)
    levels = scoped(Level).all()
    avg_fee = sum(l.annual_fee for l in levels) / len(levels) if levels else 0
    _, _, _, rec_rate = revenue_forecast()
    yrs = years()

    # recettes / dépenses par année scolaire
    rev_y, exp_y = [], []
    for y in yrs:
        a, b = date(y.start_year, 9, 1), date(y.start_year + 1, 8, 31)
        rev_y.append(db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.school_id == current_user.school_id, Payment.paid_on.between(a, b)).scalar())
        exp_y.append(db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.school_id == current_user.school_id, Expense.spent_on.between(a, b)).scalar())
    full = yrs[:-1]  # l'année en cours est incomplète
    ea, eb = linreg(list(range(len(full))), exp_y[:-1])
    exp_fc = ea * len(full) + eb
    rev_fc = cohort * avg_fee * rec_rate

    # comparaison pluriannuelle
    comp = []
    for y in yrs:
        t = 3 if y != year else latest_term(y.id)
        a = student_avgs(y.id, t)
        s, e = year_bounds(y)
        e = min(e, date.today())
        n_abs = Absence.query.filter(Absence.school_id == current_user.school_id, Absence.kind == "absence",
                                     Absence.on_date.between(s, e)).count()
        n_st = Enrollment.query.filter_by(school_id=current_user.school_id, year_id=y.id).count()
        due = Fee.query.filter(Fee.school_id == current_user.school_id, Fee.year_id == y.id,
                               Fee.due_date <= date.today()).all()
        due_amt = sum(f.amount for f in due) or 1
        comp.append({"label": y.label + (f" (T{t})" if y == year else ""),
                     "avg": round(sum(a.values()) / len(a), 2) if a else None,
                     "success": round(sum(v >= 10 for v in a.values()) / len(a) * 100, 1) if a else None,
                     "abs": round(n_abs / (n_st * (school_days(s, e) or 1)) * 100, 2) if n_st else None,
                     "rec": round(sum(f.paid_amount or 0 for f in due) / due_amt * 100, 1), "n": n_st})

    # besoins futurs
    classes = scoped(Classroom).all()
    avg_cap = sum(c.capacity for c in classes) / len(classes) if classes else 30
    hours_per_class = sum(s.weekly_hours for s in scoped(Subject).all())
    teachers = scoped(Teacher).all()
    avg_contract = sum(t.contract_hours for t in teachers) / len(teachers) if teachers else 18
    rooms = scoped(Room).filter_by(kind="Salle de cours").count() or scoped(Room).count()
    base = {"students": stats[-1]["total"], "forecast": cohort, "avg_fee": round(avg_fee),
            "recovery": round(rec_rate * 100, 1), "class_size": round(avg_cap),
            "hours_per_class": hours_per_class, "contract": round(avg_contract, 1),
            "teachers": len(teachers), "rooms": rooms, "classes": len(classes),
            "expenses": round(exp_fc), "room_slots": WEEK_SLOTS}
    need_classes = -(-cohort // round(avg_cap))
    need_teachers = -(-int(need_classes * hours_per_class) // int(avg_contract))

    # recommandations automatiques
    risks = compute_risks(year)
    recos = []
    late = late_fees_by_student()
    cur_rec = comp[-1]["rec"]
    if cohort > stats[-1]["total"] * 1.03:
        recos.append(("Effectifs", f"Hausse attendue des effectifs (+{cohort - stats[-1]['total']} élèves) : "
                                   f"prévoir {max(need_classes - len(classes), 0)} classe(s) supplémentaire(s)."))
    elif cohort < stats[-1]["total"] * 0.97:
        recos.append(("Effectifs", "Baisse attendue des effectifs : renforcer la campagne d'inscription "
                                   "(portes ouvertes, partenariats) et fidéliser les familles."))
    if need_teachers > len(teachers):
        recos.append(("Ressources humaines", f"Recruter environ {need_teachers - len(teachers)} enseignant(s) "
                                             "pour absorber la charge prévisionnelle."))
    over = [t for t in teachers if teacher_loads().get(t.id, 0) > t.contract_hours]
    if over:
        recos.append(("Ressources humaines", f"{len(over)} enseignant(s) en surcharge : rééquilibrer les "
                                             "services ou recourir à des vacataires."))
    if cur_rec < 90:
        recos.append(("Finances", f"Taux de recouvrement à {cur_rec:.0f} % : mettre en place des relances "
                                  f"automatiques et des échéanciers pour les {len(late)} élèves ayant au moins une échéance impayée."))
    if rev_fc < exp_fc:
        recos.append(("Finances", "Les dépenses prévues dépassent les recettes estimées : réviser le budget ou "
                                  "ajuster les frais de scolarité."))
    hi = sum(1 for r in risks if r["level"] == "Élevé")
    if hi:
        recos.append(("Pédagogie", f"{hi} élèves à risque élevé : ouvrir des séances de soutien ciblées "
                                   "et un suivi individualisé."))
    if len(comp) > 1 and comp[-1]["avg"] and comp[-2]["avg"] and comp[-1]["avg"] < comp[-2]["avg"]:
        recos.append(("Pédagogie", "Moyenne générale en baisse par rapport à l'an dernier : analyser les "
                                   "matières les plus faibles en conseil pédagogique."))
    conflicts, _ = detect_conflicts()
    if conflicts:
        recos.append(("Organisation", f"Corriger les {len(conflicts)} conflits d'emploi du temps détectés."))
    next_label = f"{year.start_year + 1}-{year.start_year + 2}"
    return {
        "kpis": [
            kpi("Inscriptions prévues " + next_label, cohort, f"tendance linéaire : {trend}"),
            kpi("Recettes prévues", fmt_money(rev_fc), f"{cohort} élèves × {avg_fee:.0f} DT × {rec_rate * 100:.0f} %"),
            kpi("Dépenses prévues", fmt_money(exp_fc), "tendance des années complètes"),
            kpi("Solde prévisionnel", fmt_money(rev_fc - exp_fc), None, "good" if rev_fc >= exp_fc else "bad"),
            kpi("Besoins estimés", f"{need_classes} classes / {need_teachers} ens.",
                f"actuel : {len(classes)} classes / {len(teachers)} ens."),
        ],
        "recommendations": recos,
        "simulation": base,
        "charts": [
            chart("fin", "Recettes et dépenses par année (+ prévision)", "bar",
                  [y.label for y in yrs] + [next_label + " (prév.)"],
                  [{"label": "Recettes", "data": [round(v) for v in rev_y] + [round(rev_fc)]},
                   {"label": "Dépenses", "data": [round(v) for v in exp_y] + [round(exp_fc)],
                    "backgroundColor": PALETTE[3]}]),
            chart("cmp", "Comparaison des performances entre années scolaires", "line", [c["label"] for c in comp],
                  [{"label": "Taux de réussite (%)", "data": [c["success"] for c in comp], "tension": .3},
                   {"label": "Taux de recouvrement (%)", "data": [c["rec"] for c in comp], "tension": .3}]),
        ],
        "tables": [{"title": "Comparaison pluriannuelle",
                    "head": ["Année", "Effectif", "Moyenne générale", "Taux de réussite", "Taux d'absentéisme",
                             "Recouvrement"],
                    "rows": [[c["label"], c["n"], c["avg"] or "-", f"{c['success']} %" if c["success"] else "-",
                              f"{c['abs']} %" if c["abs"] is not None else "-", f"{c['rec']} %"] for c in comp]}],
        "note": "L'année en cours est incomplète : ses recettes et dépenses ne couvrent que les mois écoulés.",
    }


# =========================================================================== registre & routes
DASHBOARDS = {
    "general": ("Vue globale de l'établissement", "🏫", dash_general, ("admin", "directeur")),
    "financier": ("Gestion financière et paiements", "💰", dash_financier, ("admin", "directeur", "comptable")),
    "pedagogique": ("Performance des élèves", "📚", dash_pedagogique, ("admin", "directeur")),
    "absences": ("Absences et retards", "🕒", dash_absences, ("admin", "directeur")),
    "enseignants": ("Enseignants – Ressources humaines", "👩‍🏫", dash_enseignants, ("admin", "directeur")),
    "inscriptions": ("Inscriptions et fidélisation", "📝", dash_inscriptions, ("admin", "directeur")),
    "communications": ("Communications avec les parents", "✉️", dash_communications, ("admin", "directeur")),
    "ressources": ("Emplois du temps et ressources", "🗓️", dash_ressources, ("admin", "directeur")),
    "risques": ("Détection des risques – Early Warning", "🚨", dash_risques, ("admin", "directeur")),
    "strategique": ("Stratégie, prévisions et aide à la décision", "🎯", dash_strategique, ("admin", "directeur")),
}


@bp.route("/<key>")
@roles_required("admin", "directeur", "comptable")
def dashboard(key):
    if key not in DASHBOARDS:
        abort(404)
    title, icon, fn, roles = DASHBOARDS[key]
    if current_user.role not in roles:
        abort(403)
    return render_template("dashboard.html", key=key, title=title, icon=icon, d=fn(),
                           dashboards=DASHBOARDS)


EXPORTS = {
    "eleves": ("id", "prenom", "nom", "sexe", "ville", "classe", "niveau", "statut"),
    "notes": ("eleve_id", "classe", "matiere", "coefficient", "annee", "trimestre", "note"),
    "paiements": ("eleve_id", "montant", "date", "moyen"),
    "echeances": ("eleve_id", "libelle", "montant", "paye", "echeance"),
    "absences": ("eleve_id", "date", "type", "justifiee"),
}


@bp.route("/export/<name>.csv")
@roles_required("admin", "directeur")
def export(name):
    """Export CSV des données brutes (alimentation Power BI / entrepôt de données)."""
    if name not in EXPORTS:
        abort(404)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(EXPORTS[name])
    sid = current_user.school_id
    if name == "eleves":
        for s in scoped(Student):
            w.writerow([s.id, s.first_name, s.last_name, s.gender, s.city,
                        s.classroom.name if s.classroom else "", s.classroom.level.name if s.classroom else "",
                        s.status])
    elif name == "notes":
        from .models import AcademicYear
        q = (db.session.query(Grade.student_id, Classroom.name, Subject.name, Subject.coefficient,
                              AcademicYear.label, Grade.term, Grade.score)
             .join(Classroom, Classroom.id == Grade.classroom_id).join(Subject, Subject.id == Grade.subject_id)
             .join(AcademicYear, AcademicYear.id == Grade.year_id).filter(Grade.school_id == sid))
        w.writerows(q)
    elif name == "paiements":
        w.writerows((p.student_id, p.amount, p.paid_on.isoformat(), p.method) for p in scoped(Payment))
    elif name == "echeances":
        w.writerows((f.student_id, f.label, f.amount, f.paid_amount, f.due_date.isoformat()) for f in scoped(Fee))
    elif name == "absences":
        w.writerows((a.student_id, a.on_date.isoformat(), a.kind, int(a.justified)) for a in scoped(Absence))
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})
