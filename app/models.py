"""Modèle de données multi-tenant.

Chaque table métier porte une colonne `school_id` : c'est le pivot de l'isolation
entre établissements (modèle « base partagée, schéma partagé »). Toutes les
requêtes applicatives passent par `scoped()` qui filtre sur l'école de
l'utilisateur connecté.
"""
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, login_manager

ROLE_LABELS = {
    "superadmin": "Super-administrateur plateforme",
    "admin": "Administrateur de l'établissement",
    "directeur": "Directeur",
    "enseignant": "Enseignant",
    "comptable": "Comptable",
    "parent": "Parent",
    "eleve": "Élève",
}

DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]


class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    city = db.Column(db.String(80))
    capacity = db.Column(db.Integer, default=400)  # capacité d'accueil globale
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True)  # NULL = superadmin
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    phone = db.Column(db.String(30))
    active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)

    school = db.relationship("School")

    def set_password(self, pwd):
        self.password_hash = generate_password_hash(pwd)

    def check_password(self, pwd):
        return check_password_hash(self.password_hash, pwd)

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


class AcademicYear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    label = db.Column(db.String(20), nullable=False)  # ex. "2026-2027"
    start_year = db.Column(db.Integer, nullable=False)
    is_current = db.Column(db.Boolean, default=False)


class Level(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    name = db.Column(db.String(60), nullable=False)
    order = db.Column(db.Integer, default=0)
    annual_fee = db.Column(db.Float, default=3000)


class Classroom(db.Model):
    """Une classe (groupe d'élèves), ex. « 7ème A »."""
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    level_id = db.Column(db.Integer, db.ForeignKey("level.id"), nullable=False)
    name = db.Column(db.String(60), nullable=False)
    capacity = db.Column(db.Integer, default=30)
    level = db.relationship("Level")


class Room(db.Model):
    """Une salle physique."""
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    name = db.Column(db.String(60), nullable=False)
    capacity = db.Column(db.Integer, default=30)
    kind = db.Column(db.String(30), default="Salle de cours")


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    name = db.Column(db.String(60), nullable=False)
    coefficient = db.Column(db.Float, default=1)
    weekly_hours = db.Column(db.Integer, default=2)


class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"))
    contract_hours = db.Column(db.Integer, default=18)  # charge hebdomadaire contractuelle
    user = db.relationship("User")
    subject = db.relationship("Subject")

    @property
    def name(self):
        return self.user.full_name


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), index=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    gender = db.Column(db.String(1))
    birth_date = db.Column(db.Date)
    city = db.Column(db.String(60))  # origine géographique
    status = db.Column(db.String(20), default="actif")  # actif | parti | abandon
    classroom = db.relationship("Classroom")
    parent = db.relationship("User", foreign_keys=[parent_id])
    user = db.relationship("User", foreign_keys=[user_id])

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Enrollment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True, nullable=False)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), index=True, nullable=False)
    level_id = db.Column(db.Integer, db.ForeignKey("level.id"))
    kind = db.Column(db.String(20), default="nouvelle")  # nouvelle | reinscription
    outcome = db.Column(db.String(20), default="en cours")  # en cours | admis | redouble | parti | abandon
    year = db.relationship("AcademicYear")


class TimetableSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teacher.id"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("room.id"), nullable=False)
    day = db.Column(db.Integer, nullable=False)  # 0 = lundi
    hour = db.Column(db.Integer, nullable=False)  # heure de début (créneau d'1 h)
    classroom = db.relationship("Classroom")
    subject = db.relationship("Subject")
    teacher = db.relationship("Teacher")
    room = db.relationship("Room")


class Fee(db.Model):
    """Échéance de frais de scolarité (mensualité)."""
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True, nullable=False)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), index=True)
    label = db.Column(db.String(60))
    amount = db.Column(db.Float, nullable=False)
    paid_amount = db.Column(db.Float, default=0)
    due_date = db.Column(db.Date, nullable=False)
    student = db.relationship("Student")

    @property
    def remaining(self):
        return round(self.amount - (self.paid_amount or 0), 2)

    @property
    def is_late(self):
        return self.remaining > 0 and self.due_date < date.today()


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    fee_id = db.Column(db.Integer, db.ForeignKey("fee.id"), index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True)
    amount = db.Column(db.Float, nullable=False)
    paid_on = db.Column(db.Date, nullable=False, index=True)
    method = db.Column(db.String(20), default="Espèces")
    student = db.relationship("Student")


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    category = db.Column(db.String(40))
    amount = db.Column(db.Float, nullable=False)
    spent_on = db.Column(db.Date, nullable=False)


class Grade(db.Model):
    """Note trimestrielle d'un élève dans une matière (sur 20)."""
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classroom.id"), index=True)
    year_id = db.Column(db.Integer, db.ForeignKey("academic_year.id"), index=True)
    term = db.Column(db.Integer, nullable=False)  # 1, 2, 3
    score = db.Column(db.Float, nullable=False)
    subject = db.relationship("Subject")
    student = db.relationship("Student")


class Absence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), index=True, nullable=False)
    on_date = db.Column(db.Date, nullable=False, index=True)
    kind = db.Column(db.String(10), default="absence")  # absence | retard
    justified = db.Column(db.Boolean, default=False)
    reason = db.Column(db.String(120))
    student = db.relationship("Student")


class TeacherAbsence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teacher.id"), nullable=False)
    on_date = db.Column(db.Date, nullable=False)
    hours = db.Column(db.Integer, default=4)
    replacement_id = db.Column(db.Integer, db.ForeignKey("teacher.id"))
    teacher = db.relationship("Teacher", foreign_keys=[teacher_id])
    replacement = db.relationship("Teacher", foreign_keys=[replacement_id])


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    subject = db.Column(db.String(150))
    body = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    read_at = db.Column(db.DateTime)
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])


class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("school.id"), index=True, nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    category = db.Column(db.String(40), default="Autre")
    subject = db.Column(db.String(150))
    body = db.Column(db.Text)
    status = db.Column(db.String(20), default="ouverte")  # ouverte | en cours | résolue
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)
    parent = db.relationship("User")
