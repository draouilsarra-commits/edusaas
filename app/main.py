"""Modules de gestion scolaire : élèves, enseignants, classes, emplois du temps,
paiements, notes, absences, bulletins, messagerie et réclamations."""
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from . import db
from .models import (DAYS, Absence, Classroom, Complaint, Fee, Grade, Level, Message, Payment,
                     Room, Student, Subject, Teacher, TimetableSlot, User)
from .services import bulletin as compute_bulletin
from .utils import current_year, get_scoped_or_404, mention, roles_required, scoped

bp = Blueprint("main", __name__)
HOURS = [8, 9, 10, 11, 14, 15]  # créneaux d'une heure


# --------------------------------------------------------------------------- helpers
def my_teacher():
    return Teacher.query.filter_by(user_id=current_user.id).first()


def teacher_class_ids(teacher):
    return {cid for (cid,) in db.session.query(TimetableSlot.classroom_id)
            .filter_by(teacher_id=teacher.id).distinct()}


def my_children():
    return scoped(Student).filter_by(parent_id=current_user.id, status="actif").all()


def can_view_student(s: Student) -> bool:
    r = current_user.role
    if s.school_id != current_user.school_id:
        return False
    if r in ("admin", "directeur", "comptable"):
        return True
    if r == "enseignant":
        t = my_teacher()
        return bool(t) and s.classroom_id in teacher_class_ids(t)
    if r == "parent":
        return s.parent_id == current_user.id
    if r == "eleve":
        return s.user_id == current_user.id
    return False


# --------------------------------------------------------------------------- accueil
@bp.route("/")
@login_required
def home():
    r = current_user.role
    if r == "superadmin":
        return redirect(url_for("platform.schools"))
    if r in ("admin", "directeur"):
        return redirect(url_for("bi.dashboard", key="general"))
    if r == "comptable":
        return redirect(url_for("bi.dashboard", key="financier"))
    year = current_year()
    ctx = {"year": year}
    if r == "enseignant":
        t = my_teacher()
        ctx["teacher"] = t
        today = date.today().weekday()
        ctx["today_slots"] = (scoped(TimetableSlot).filter_by(teacher_id=t.id, day=today)
                              .order_by(TimetableSlot.hour).all()) if t else []
        ctx["classes"] = scoped(Classroom).filter(Classroom.id.in_(teacher_class_ids(t))).all() if t else []
        ctx["weekly_hours"] = scoped(TimetableSlot).filter_by(teacher_id=t.id).count() if t else 0
    elif r in ("parent", "eleve"):
        kids = my_children() if r == "parent" else scoped(Student).filter_by(user_id=current_user.id).all()
        summary = []
        for s in kids:
            avg = (db.session.query(func.avg(Grade.score))
                   .filter_by(student_id=s.id, year_id=year.id).scalar())
            abs_n = scoped(Absence).filter_by(student_id=s.id, kind="absence").filter(
                Absence.on_date >= date(year.start_year, 9, 1)).count()
            due = sum(f.remaining for f in scoped(Fee).filter_by(student_id=s.id, year_id=year.id)
                      if f.due_date <= date.today())
            summary.append({"s": s, "avg": round(avg, 2) if avg else None, "absences": abs_n, "due": due})
        ctx["summary"] = summary
    ctx["unread"] = scoped(Message).filter_by(recipient_id=current_user.id, read_at=None).count()
    return render_template("home.html", **ctx)


# --------------------------------------------------------------------------- élèves
@bp.route("/eleves")
@roles_required("admin", "directeur", "comptable", "enseignant")
def students():
    q = scoped(Student).filter_by(status="actif")
    if current_user.role == "enseignant":
        q = q.filter(Student.classroom_id.in_(teacher_class_ids(my_teacher())))
    cid = request.args.get("classe", type=int)
    if cid:
        q = q.filter_by(classroom_id=cid)
    term = request.args.get("q", "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(or_(Student.first_name.ilike(like), Student.last_name.ilike(like)))
    page = q.order_by(Student.last_name).paginate(page=request.args.get("page", 1, type=int), per_page=25)
    return render_template("students.html", page=page,
                           classes=scoped(Classroom).order_by(Classroom.name).all())


@bp.route("/eleves/nouveau", methods=["GET", "POST"])
@roles_required("admin")
def student_new():
    classes = scoped(Classroom).order_by(Classroom.name).all()
    if request.method == "POST":
        f = request.form
        cls = get_scoped_or_404(Classroom, int(f["classroom_id"]))
        parent = None
        if f.get("parent_email"):
            email = f["parent_email"].strip().lower()
            parent = User.query.filter_by(email=email).first()
            if parent and parent.school_id != current_user.school_id:
                flash("Cet e-mail est déjà utilisé dans un autre établissement.", "danger")
                return render_template("student_form.html", classes=classes)
            if not parent:
                parent = User(school_id=current_user.school_id, email=email, role="parent",
                              full_name=f.get("parent_name") or f"Parent de {f['first_name']}")
                parent.set_password(f.get("parent_password") or "Parent@2026")
                db.session.add(parent)
                db.session.flush()
        s = Student(school_id=current_user.school_id, first_name=f["first_name"], last_name=f["last_name"],
                    gender=f.get("gender"), city=f.get("city"), classroom_id=cls.id,
                    birth_date=datetime.strptime(f["birth_date"], "%Y-%m-%d").date() if f.get("birth_date") else None,
                    parent_id=parent.id if parent else None)
        db.session.add(s)
        db.session.flush()
        year = current_year()
        from .models import Enrollment
        db.session.add(Enrollment(school_id=s.school_id, student_id=s.id, year_id=year.id,
                                  level_id=cls.level_id, kind="nouvelle"))
        # génération automatique des 10 mensualités de l'année
        fee = cls.level.annual_fee / 10
        for i in range(10):
            m = (9 + i - 1) % 12 + 1
            y = year.start_year + (1 if m < 9 else 0)
            db.session.add(Fee(school_id=s.school_id, student_id=s.id, year_id=year.id,
                               label=f"Mensualité {m:02d}/{y}", amount=fee, due_date=date(y, m, 5)))
        db.session.commit()
        flash("Élève inscrit et échéancier de paiement généré.", "success")
        return redirect(url_for(".student_detail", sid=s.id))
    return render_template("student_form.html", classes=classes)


@bp.route("/eleves/<int:sid>")
@login_required
def student_detail(sid):
    s = get_scoped_or_404(Student, sid)
    if not can_view_student(s):
        abort(403)
    year = current_year()
    grades = (Grade.query.filter_by(student_id=s.id, year_id=year.id)
              .order_by(Grade.term, Grade.subject_id).all())
    absences = scoped(Absence).filter_by(student_id=s.id).order_by(Absence.on_date.desc()).limit(30).all()
    fees = scoped(Fee).filter_by(student_id=s.id, year_id=year.id).order_by(Fee.due_date).all()
    show_fees = current_user.role in ("admin", "directeur", "comptable", "parent")
    return render_template("student_detail.html", s=s, grades=grades, absences=absences,
                           fees=fees if show_fees else None, year=year)


# --------------------------------------------------------------------------- enseignants
@bp.route("/enseignants", methods=["GET", "POST"])
@roles_required("admin", "directeur")
def teachers():
    if request.method == "POST":
        if current_user.role != "admin":
            abort(403)
        f = request.form
        if User.query.filter_by(email=f["email"].strip().lower()).first():
            flash("E-mail déjà utilisé.", "danger")
        else:
            u = User(school_id=current_user.school_id, email=f["email"].strip().lower(),
                     full_name=f["full_name"], role="enseignant")
            u.set_password(f["password"])
            db.session.add(u)
            db.session.flush()
            db.session.add(Teacher(school_id=current_user.school_id, user_id=u.id,
                                   subject_id=int(f["subject_id"]), contract_hours=int(f["contract_hours"])))
            db.session.commit()
            flash("Enseignant ajouté.", "success")
        return redirect(url_for(".teachers"))
    load = dict(db.session.query(TimetableSlot.teacher_id, func.count(TimetableSlot.id))
                .filter_by(school_id=current_user.school_id).group_by(TimetableSlot.teacher_id).all())
    return render_template("teachers.html", teachers=scoped(Teacher).all(), load=load,
                           subjects=scoped(Subject).all())


# --------------------------------------------------------------------------- classes & salles
@bp.route("/classes", methods=["GET", "POST"])
@roles_required("admin", "directeur")
def classes():
    if request.method == "POST":
        if current_user.role != "admin":
            abort(403)
        f = request.form
        if f.get("kind") == "room":
            db.session.add(Room(school_id=current_user.school_id, name=f["name"],
                                capacity=int(f["capacity"]), kind=f.get("room_kind", "Salle de cours")))
        else:
            get_scoped_or_404(Level, int(f["level_id"]))
            db.session.add(Classroom(school_id=current_user.school_id, name=f["name"],
                                     level_id=int(f["level_id"]), capacity=int(f["capacity"])))
        db.session.commit()
        flash("Enregistré.", "success")
        return redirect(url_for(".classes"))
    counts = dict(db.session.query(Student.classroom_id, func.count(Student.id))
                  .filter_by(school_id=current_user.school_id, status="actif")
                  .group_by(Student.classroom_id).all())
    return render_template("classes.html",
                           classes=scoped(Classroom).join(Level).order_by(Level.order, Classroom.name).all(),
                           rooms=scoped(Room).order_by(Room.name).all(), counts=counts,
                           levels=scoped(Level).order_by(Level.order).all())


# --------------------------------------------------------------------------- utilisateurs
@bp.route("/utilisateurs", methods=["GET", "POST"])
@roles_required("admin")
def users():
    if request.method == "POST":
        f = request.form
        role = f["role"]
        if role not in ("admin", "directeur", "comptable", "parent"):
            abort(400)
        if User.query.filter_by(email=f["email"].strip().lower()).first():
            flash("E-mail déjà utilisé.", "danger")
        else:
            u = User(school_id=current_user.school_id, email=f["email"].strip().lower(),
                     full_name=f["full_name"], role=role)
            u.set_password(f["password"])
            db.session.add(u)
            db.session.commit()
            flash("Compte créé.", "success")
        return redirect(url_for(".users"))
    role = request.args.get("role")
    q = scoped(User)
    if role:
        q = q.filter_by(role=role)
    page = q.order_by(User.role, User.full_name).paginate(page=request.args.get("page", 1, type=int), per_page=30)
    return render_template("users.html", page=page, role=role)


@bp.route("/utilisateurs/<int:uid>/toggle", methods=["POST"])
@roles_required("admin")
def user_toggle(uid):
    u = get_scoped_or_404(User, uid)
    if u.id == current_user.id:
        flash("Vous ne pouvez pas désactiver votre propre compte.", "warning")
    else:
        u.active = not u.active
        db.session.commit()
    return redirect(request.referrer or url_for(".users"))


# --------------------------------------------------------------------------- emploi du temps
@bp.route("/emploi-du-temps", methods=["GET", "POST"])
@login_required
def timetable():
    r = current_user.role
    if r == "superadmin":
        abort(403)
    if request.method == "POST":
        if r != "admin":
            abort(403)
        f = request.form
        day, hour = int(f["day"]), int(f["hour"])
        cls = get_scoped_or_404(Classroom, int(f["classroom_id"]))
        t = get_scoped_or_404(Teacher, int(f["teacher_id"]))
        room = get_scoped_or_404(Room, int(f["room_id"]))
        subj = get_scoped_or_404(Subject, int(f["subject_id"]))
        base = scoped(TimetableSlot).filter_by(day=day, hour=hour)
        conflicts = []
        if base.filter_by(classroom_id=cls.id).first():
            conflicts.append("la classe a déjà cours")
        if base.filter_by(teacher_id=t.id).first():
            conflicts.append("l'enseignant est déjà occupé")
        if base.filter_by(room_id=room.id).first():
            conflicts.append("la salle est déjà occupée")
        if conflicts and not f.get("force"):
            flash("Conflit détecté : " + ", ".join(conflicts) + ".", "danger")
        else:
            db.session.add(TimetableSlot(school_id=current_user.school_id, classroom_id=cls.id,
                                         subject_id=subj.id, teacher_id=t.id, room_id=room.id,
                                         day=day, hour=hour))
            db.session.commit()
            flash("Créneau ajouté.", "success")
        return redirect(url_for(".timetable", classe=cls.id))

    q = scoped(TimetableSlot)
    title = ""
    classes = scoped(Classroom).order_by(Classroom.name).all()
    teachers = scoped(Teacher).all()
    if r == "enseignant":
        t = my_teacher()
        q = q.filter_by(teacher_id=t.id)
        title = f"Emploi du temps de {t.name}"
    elif r in ("parent", "eleve"):
        kids = my_children() if r == "parent" else scoped(Student).filter_by(user_id=current_user.id).all()
        cid = request.args.get("classe", type=int) or (kids[0].classroom_id if kids else None)
        if cid not in {k.classroom_id for k in kids}:
            abort(403)
        q = q.filter_by(classroom_id=cid)
        classes = [k.classroom for k in kids]
        title = f"Emploi du temps – {db.session.get(Classroom, cid).name}"
    else:
        tid = request.args.get("enseignant", type=int)
        cid = request.args.get("classe", type=int) or (classes[0].id if classes and not tid else None)
        if tid:
            q = q.filter_by(teacher_id=tid)
            title = f"Emploi du temps de {get_scoped_or_404(Teacher, tid).name}"
        else:
            q = q.filter_by(classroom_id=cid)
            title = f"Emploi du temps – {get_scoped_or_404(Classroom, cid).name}" if cid else ""
    grid = {}
    for s in q.all():
        grid.setdefault((s.day, s.hour), []).append(s)
    return render_template("timetable.html", grid=grid, days=DAYS, hours=HOURS, title=title,
                           classes=classes, teachers=teachers,
                           subjects=scoped(Subject).all(), rooms=scoped(Room).all())


# --------------------------------------------------------------------------- paiements
@bp.route("/paiements")
@roles_required("admin", "comptable", "directeur")
def payments():
    year = current_year()
    q = scoped(Fee).filter_by(year_id=year.id).join(Student).filter(Student.status == "actif")
    flt = request.args.get("filtre", "retard")
    if flt == "retard":
        q = q.filter(Fee.due_date < date.today(), Fee.paid_amount < Fee.amount)
    elif flt == "a_venir":
        q = q.filter(Fee.due_date >= date.today())
    term = request.args.get("q", "").strip()
    if term:
        q = q.filter(or_(Student.first_name.ilike(f"%{term}%"), Student.last_name.ilike(f"%{term}%")))
    page = q.order_by(Fee.due_date, Student.last_name).paginate(
        page=request.args.get("page", 1, type=int), per_page=30)
    recent = scoped(Payment).order_by(Payment.paid_on.desc(), Payment.id.desc()).limit(10).all()
    return render_template("payments.html", page=page, flt=flt, recent=recent)


@bp.route("/paiements/<int:fid>/encaisser", methods=["POST"])
@roles_required("admin", "comptable")
def pay(fid):
    fee = get_scoped_or_404(Fee, fid)
    amount = min(float(request.form["amount"]), fee.remaining)
    if amount <= 0:
        flash("Montant invalide.", "warning")
    else:
        fee.paid_amount = (fee.paid_amount or 0) + amount
        db.session.add(Payment(school_id=fee.school_id, fee_id=fee.id, student_id=fee.student_id,
                               amount=amount, paid_on=date.today(), method=request.form.get("method", "Espèces")))
        db.session.commit()
        flash(f"Paiement de {amount:.0f} DT enregistré pour {fee.student.full_name}.", "success")
    return redirect(request.referrer or url_for(".payments"))


# --------------------------------------------------------------------------- notes
@bp.route("/notes", methods=["GET", "POST"])
@roles_required("enseignant", "admin")
def grades():
    year = current_year()
    if current_user.role == "enseignant":
        t = my_teacher()
        classes = scoped(Classroom).filter(Classroom.id.in_(teacher_class_ids(t))).order_by(Classroom.name).all()
        subjects = [t.subject]
    else:
        classes = scoped(Classroom).order_by(Classroom.name).all()
        subjects = scoped(Subject).all()
    cid = request.values.get("classe", type=int) or (classes[0].id if classes else None)
    subj_id = request.values.get("matiere", type=int) or (subjects[0].id if subjects else None)
    term = request.values.get("trimestre", 1, type=int)
    if cid not in {c.id for c in classes} or subj_id not in {s.id for s in subjects}:
        abort(403)
    students = scoped(Student).filter_by(classroom_id=cid, status="actif").order_by(Student.last_name).all()
    existing = {g.student_id: g for g in Grade.query.filter_by(
        classroom_id=cid, subject_id=subj_id, year_id=year.id, term=term)}
    if request.method == "POST":
        n = 0
        for s in students:
            raw = request.form.get(f"note_{s.id}", "").replace(",", ".").strip()
            if raw == "":
                continue
            score = max(0.0, min(20.0, float(raw)))
            g = existing.get(s.id)
            if g:
                g.score = score
            else:
                db.session.add(Grade(school_id=current_user.school_id, student_id=s.id, subject_id=subj_id,
                                     classroom_id=cid, year_id=year.id, term=term, score=score))
            n += 1
        db.session.commit()
        flash(f"{n} note(s) enregistrée(s).", "success")
        return redirect(url_for(".grades", classe=cid, matiere=subj_id, trimestre=term))
    return render_template("grades.html", classes=classes, subjects=subjects, cid=cid, subj_id=subj_id,
                           term=term, students=students, existing=existing)


# --------------------------------------------------------------------------- absences
@bp.route("/absences", methods=["GET", "POST"])
@roles_required("enseignant", "admin", "directeur")
def absences():
    if current_user.role == "enseignant":
        classes = scoped(Classroom).filter(Classroom.id.in_(teacher_class_ids(my_teacher()))).all()
    else:
        classes = scoped(Classroom).order_by(Classroom.name).all()
    cid = request.values.get("classe", type=int) or (classes[0].id if classes else None)
    if cid not in {c.id for c in classes}:
        abort(403)
    day = datetime.strptime(request.values.get("date", date.today().isoformat()), "%Y-%m-%d").date()
    students = scoped(Student).filter_by(classroom_id=cid, status="actif").order_by(Student.last_name).all()
    if request.method == "POST":
        if current_user.role == "directeur":
            abort(403)
        n = 0
        for s in students:
            kind = request.form.get(f"st_{s.id}")
            if kind in ("absence", "retard"):
                if not scoped(Absence).filter_by(student_id=s.id, on_date=day).first():
                    db.session.add(Absence(school_id=current_user.school_id, student_id=s.id,
                                           on_date=day, kind=kind))
                    n += 1
                    if s.parent_id:
                        db.session.add(Message(
                            school_id=current_user.school_id, sender_id=current_user.id, recipient_id=s.parent_id,
                            subject=f"{kind.capitalize()} de {s.full_name}",
                            body=f"Nous vous informons que {s.full_name} a été noté(e) en {kind} le {day:%d/%m/%Y}."))
        db.session.commit()
        flash(f"{n} absence(s)/retard(s) enregistré(s) – parents notifiés.", "success")
        return redirect(url_for(".absences", classe=cid, date=day.isoformat()))
    recent = (scoped(Absence).join(Student).filter(Student.classroom_id == cid)
              .order_by(Absence.on_date.desc()).limit(40).all())
    return render_template("absences.html", classes=classes, cid=cid, day=day, students=students, recent=recent)


@bp.route("/absences/<int:aid>/justifier", methods=["POST"])
@roles_required("admin")
def justify(aid):
    a = get_scoped_or_404(Absence, aid)
    a.justified = True
    a.reason = request.form.get("reason") or "Justificatif reçu"
    db.session.commit()
    return redirect(request.referrer or url_for(".absences"))


# --------------------------------------------------------------------------- bulletins
@bp.route("/bulletin/<int:sid>")
@login_required
def bulletin(sid):
    s = get_scoped_or_404(Student, sid)
    if not can_view_student(s):
        abort(403)
    year = current_year()
    term = request.args.get("trimestre", 1, type=int)
    b = compute_bulletin(s, year.id, term)
    return render_template("bulletin.html", s=s, b=b, term=term, year=year, mention=mention)


@bp.route("/bulletins")
@roles_required("admin", "directeur", "enseignant")
def bulletins():
    classes = scoped(Classroom).order_by(Classroom.name).all()
    if current_user.role == "enseignant":
        ids = teacher_class_ids(my_teacher())
        classes = [c for c in classes if c.id in ids]
    cid = request.args.get("classe", type=int) or (classes[0].id if classes else None)
    term = request.args.get("trimestre", 1, type=int)
    year = current_year()
    from .services import class_term_averages
    avgs = class_term_averages(cid, year.id, term) if cid else {}
    students = scoped(Student).filter_by(classroom_id=cid, status="actif").all() if cid else []
    rows = sorted([(s, avgs.get(s.id)) for s in students], key=lambda x: -(x[1] or 0))
    return render_template("bulletins.html", classes=classes, cid=cid, term=term, rows=rows, mention=mention)


# --------------------------------------------------------------------------- messagerie
@bp.route("/messages", methods=["GET", "POST"])
@login_required
def messages():
    if current_user.role == "superadmin":
        abort(403)
    if request.method == "POST":
        f = request.form
        target = f.get("target")
        recipients = []
        if current_user.role in ("parent", "eleve"):
            recipients = scoped(User).filter(User.role.in_(["admin", "directeur"])).all()
        elif target == "all_parents" and current_user.role in ("admin", "directeur"):
            recipients = scoped(User).filter_by(role="parent", active=True).all()
        elif target and target.startswith("class_"):
            cid = int(target.split("_")[1])
            get_scoped_or_404(Classroom, cid)
            if current_user.role == "enseignant" and cid not in teacher_class_ids(my_teacher()):
                abort(403)
            pids = {s.parent_id for s in scoped(Student).filter_by(classroom_id=cid, status="actif") if s.parent_id}
            recipients = scoped(User).filter(User.id.in_(pids)).all()
        elif f.get("recipient_id"):
            recipients = [get_scoped_or_404(User, int(f["recipient_id"]))]
        for u in recipients:
            db.session.add(Message(school_id=current_user.school_id, sender_id=current_user.id,
                                   recipient_id=u.id, subject=f["subject"], body=f["body"]))
        db.session.commit()
        flash(f"Message envoyé à {len(recipients)} destinataire(s).", "success")
        return redirect(url_for(".messages"))
    inbox = (scoped(Message).filter_by(recipient_id=current_user.id)
             .order_by(Message.sent_at.desc()).limit(50).all())
    sent_count = scoped(Message).filter_by(sender_id=current_user.id).count()
    classes = []
    if current_user.role in ("admin", "directeur"):
        classes = scoped(Classroom).order_by(Classroom.name).all()
    elif current_user.role == "enseignant":
        classes = scoped(Classroom).filter(Classroom.id.in_(teacher_class_ids(my_teacher()))).all()
    return render_template("messages.html", inbox=inbox, sent_count=sent_count, classes=classes)


@bp.route("/messages/<int:mid>")
@login_required
def message_read(mid):
    m = get_scoped_or_404(Message, mid)
    if m.recipient_id != current_user.id:
        abort(403)
    if not m.read_at:
        m.read_at = datetime.utcnow()
        db.session.commit()
    return render_template("message.html", m=m)


# --------------------------------------------------------------------------- réclamations
@bp.route("/reclamations", methods=["GET", "POST"])
@roles_required("parent", "admin", "directeur")
def complaints():
    if request.method == "POST":
        f = request.form
        if current_user.role == "parent":
            db.session.add(Complaint(school_id=current_user.school_id, parent_id=current_user.id,
                                     category=f["category"], subject=f["subject"], body=f["body"]))
            flash("Réclamation transmise à l'administration.", "success")
        else:
            c = get_scoped_or_404(Complaint, int(f["id"]))
            c.status = f["status"]
            c.resolved_at = datetime.utcnow() if c.status == "résolue" else None
            flash("Statut mis à jour.", "success")
        db.session.commit()
        return redirect(url_for(".complaints"))
    q = scoped(Complaint)
    if current_user.role == "parent":
        q = q.filter_by(parent_id=current_user.id)
    status = request.args.get("statut")
    if status:
        q = q.filter_by(status=status)
    return render_template("complaints.html", items=q.order_by(Complaint.created_at.desc()).limit(100).all())
