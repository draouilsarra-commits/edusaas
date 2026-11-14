"""Génération de données de démonstration réalistes (2 établissements, 4 années).

Usage :
    python -m app.seed            # (ré)initialise complètement la base
    python -m app.seed --if-empty # ne fait rien si des données existent déjà

Toutes les personnes sont fictives. La simulation est déterministe (graine fixe)
et produit volontairement des situations à détecter par le module BI : classes
surchargées, retards de paiement, élèves en décrochage, conflits d'emploi du
temps, parents qui ne lisent pas les messages, etc.
"""
import math
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

from . import create_app, db
from .models import (AcademicYear, Absence, Classroom, Complaint, Enrollment, Expense, Fee, Grade,
                     Level, Message, Payment, Room, School, Student, Subject, Teacher, TeacherAbsence,
                     TimetableSlot, User)
from .platform import DEFAULT_LEVELS, DEFAULT_SUBJECTS

DEMO_PASSWORDS = {
    "superadmin": "Super#2026", "admin": "Admin#2026", "directeur": "Direct#2026",
    "enseignant": "Prof#2026", "comptable": "Compta#2026", "parent": "Parent#2026", "eleve": "Eleve#2026",
}
SHARED_PASSWORD = "Demo#2026"  # tous les autres comptes générés

FIRST_M = ["Mohamed", "Ahmed", "Youssef", "Amine", "Omar", "Hamza", "Aziz", "Rayen", "Skander", "Iyed", "Adam",
           "Yassine", "Khalil", "Mehdi", "Anas", "Ilyes", "Fares", "Wassim", "Nour", "Selim", "Oussama", "Ayoub",
           "Bilel", "Hedi", "Malek", "Taha", "Zied", "Firas", "Karim", "Achref"]
FIRST_F = ["Yasmine", "Mariem", "Eya", "Nour", "Sarra", "Ines", "Rania", "Amira", "Chaima", "Malak", "Lina",
           "Farah", "Salma", "Hiba", "Asma", "Rim", "Oumaima", "Meriem", "Aya", "Jihene", "Emna", "Fatma",
           "Ons", "Syrine", "Tasnim", "Nadia", "Dorra", "Khadija", "Leila", "Mouna"]
LAST = ["Ben Ali", "Trabelsi", "Gharbi", "Jaziri", "Hammami", "Mejri", "Ayari", "Bouazizi", "Khelifi", "Chaabane",
        "Sassi", "Dridi", "Ferchichi", "Baccouche", "Mansouri", "Jebali", "Ben Salah", "Haddad", "Zouari",
        "Masmoudi", "Karoui", "Ben Amor", "Bouzid", "Lahmar", "Saidi", "Rezgui", "Guesmi", "Abidi", "Mzoughi",
        "Ouertani", "Ben Youssef", "Chebbi", "Souissi", "Fourati", "Nasri", "Hajji", "Brahmi", "Sfaxi"]

SCHOOLS = [
    dict(name="Collège-Lycée Ibn Khaldoun", code="ibnkhaldoun", city="Monastir", capacity=420,
         classes_per_level=[2, 2, 2, 2, 2, 2], class_cap=30, seed_size=[58, 56, 54, 56, 50, 46],
         base_new=[53, 3, 3, 10, 2, 1], growth=0.04, depart=0.05, salary=2200, staff=18000,
         cities=[("Monastir", 40), ("Sahline", 12), ("Ksar Hellal", 10), ("Moknine", 9), ("Jemmal", 8),
                 ("Skanès", 8), ("Sousse", 6), ("Mahdia", 4), ("Bembla", 3)]),
    dict(name="École Privée Al Amal", code="alamal", city="Sousse", capacity=260,
         classes_per_level=[2, 1, 1, 2, 1, 1], class_cap=28, seed_size=[48, 36, 34, 44, 32, 30],
         base_new=[38, 2, 1, 13, 1, 0], growth=-0.07, depart=0.09, salary=2000, staff=7000,
         cities=[("Sousse", 45), ("Hammam Sousse", 15), ("Msaken", 12), ("Kalâa Kebira", 10), ("Akouda", 8),
                 ("Monastir", 5), ("Enfidha", 5)]),
]
SUBJECT_OFFSET = {"Mathématiques": -1.3, "Physique": -0.9, "Français": -0.3, "Sciences": 0.0, "Anglais": 0.5,
                  "Arabe": 0.6, "Histoire-Géographie": 0.4, "Informatique": 1.4}
NOTICES = ["Réunion parents-professeurs", "Calendrier des examens", "Sortie pédagogique", "Rappel : paiement des "
           "frais de scolarité", "Journée portes ouvertes", "Vacances scolaires", "Résultats du trimestre disponibles",
           "Activités parascolaires", "Vaccination scolaire", "Fête de fin d'année"]
COMPLAINT_TOPICS = {
    "Pédagogie": ["Demande de cours de soutien", "Note contestée en mathématiques", "Charge de devoirs trop lourde"],
    "Paiement": ["Demande d'échéancier", "Erreur sur le reçu de paiement", "Réduction fratrie"],
    "Transport": ["Retard du bus scolaire", "Changement d'arrêt de bus"],
    "Discipline": ["Conflit entre élèves", "Sanction jugée injustifiée"],
    "Emploi du temps": ["Trop de trous dans l'emploi du temps", "Cours le samedi après-midi"],
    "Autre": ["Objet perdu", "Demande de certificat de scolarité", "Qualité de la cantine"],
}
HOURS = [8, 9, 10, 11, 14, 15]


def weekdays_between(rng, start, end, k):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    return [rng.choice(days) for _ in range(k)] if days else []


def poisson(rng, lam):
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def seed_school(cfg, rng, hashes, today, cur_start):
    year_starts = [cur_start - 3, cur_start - 2, cur_start - 1, cur_start]
    school = School(name=cfg["name"], code=cfg["code"], city=cfg["city"], capacity=cfg["capacity"],
                    created_at=datetime(cur_start - 3, 7, 1))
    db.session.add(school)
    db.session.flush()
    sid = school.id
    years = [AcademicYear(school_id=sid, label=f"{y}-{y + 1}", start_year=y, is_current=(y == cur_start))
             for y in year_starts]
    levels = [Level(school_id=sid, name=n, order=i, annual_fee=2800 + 200 * i) for i, n in enumerate(DEFAULT_LEVELS)]
    subjects = [Subject(school_id=sid, name=n, coefficient=c, weekly_hours=h) for n, c, h in DEFAULT_SUBJECTS]
    db.session.add_all(years + levels + subjects)
    db.session.flush()
    classes_by_level = []
    for li, n in enumerate(cfg["classes_per_level"]):
        cl = [Classroom(school_id=sid, level_id=levels[li].id, name=f"{DEFAULT_LEVELS[li].split()[0]} {'ABC'[k]}",
                        capacity=cfg["class_cap"]) for k in range(n)]
        db.session.add_all(cl)
        classes_by_level.append(cl)
    db.session.flush()
    all_classes = [c for cl in classes_by_level for c in cl]
    rooms = [Room(school_id=sid, name=f"Salle {i + 1}", capacity=cfg["class_cap"] + 2) for i in range(len(all_classes))]
    lab = Room(school_id=sid, name="Laboratoire", capacity=30, kind="Laboratoire")
    info = Room(school_id=sid, name="Salle informatique", capacity=24, kind="Salle informatique")
    spare = Room(school_id=sid, name="Salle polyvalente", capacity=40)
    db.session.add_all(rooms + [lab, info, spare])
    db.session.flush()

    code = cfg["code"]

    def mk_user(role, email, name, demo=False, phone=None):
        u = User(school_id=sid, email=email, full_name=name, role=role,
                 password_hash=hashes[role] if demo else hashes["shared"], phone=phone)
        db.session.add(u)
        return u

    def tel():
        return f"+216 {rng.choice([2, 5, 9])}{rng.randint(0, 9)} {rng.randint(100, 999)} {rng.randint(100, 999)}"

    mk_user("admin", f"admin@{code}.demo", f"{rng.choice(FIRST_F)} {rng.choice(LAST)}", True)
    mk_user("directeur", f"directeur@{code}.demo", f"{rng.choice(FIRST_M)} {rng.choice(LAST)}", True)
    mk_user("comptable", f"comptable@{code}.demo", f"{rng.choice(FIRST_F)} {rng.choice(LAST)}", True)

    # ---------------------------------------------------------------- enseignants
    teachers_by_subj = {}
    first_math = True
    for s in subjects:
        hours = s.weekly_hours * len(all_classes)
        n = max(1, math.ceil(hours / 20))
        lst = []
        for _ in range(n):
            g = rng.random() < 0.55
            name = f"{rng.choice(FIRST_F if g else FIRST_M)} {rng.choice(LAST)}"
            demo = s.name == "Mathématiques" and first_math
            first_math = first_math and not demo
            email = f"prof@{code}.demo" if demo else f"{name.split()[0].lower()}.{len(lst) + 1}.{s.id}@{code}.demo"
            u = mk_user("enseignant", email, name, demo, tel())
            db.session.flush()
            t = Teacher(school_id=sid, user_id=u.id, subject_id=s.id,
                        contract_hours=rng.choice([15, 18, 18, 18, 20]) if s.weekly_hours > 1 else 12)
            db.session.add(t)
            lst.append(t)
        teachers_by_subj[s.id] = lst
    db.session.flush()
    all_teachers = [t for l in teachers_by_subj.values() for t in l]

    # ---------------------------------------------------------------- simulation de la population
    def new_student(level, kind, sy):
        g = rng.random() < 0.5
        return {"first": rng.choice(FIRST_F if g else FIRST_M), "last": rng.choice(LAST), "gender": "F" if g else "M",
                "ability": max(4.5, min(18.5, rng.gauss(12.4, 2.6))), "eng": rng.gauss(0, 1),
                "city": rng.choices([c for c, _ in cfg["cities"]], [w for _, w in cfg["cities"]])[0],
                "birth": date(sy - 12 - level + rng.randint(-1, 0), rng.randint(1, 12), rng.randint(1, 28)),
                "status": "actif", "records": {}}

    def year_ability(s):
        return s["ability"] + rng.gauss(0, 0.8)

    population, active = [], []
    for yi, sy in enumerate(year_starts):
        nxt = []
        if yi == 0:
            for li, n in enumerate(cfg["seed_size"]):
                for _ in range(n):
                    s = new_student(li, None, sy)
                    s["records"][sy] = {"level": li, "kind": "reinscription" if rng.random() < .8 else "nouvelle"}
                    population.append(s); nxt.append(s)
        else:
            prev = year_starts[yi - 1]
            for s in active:
                rec = s["records"][prev]
                li, ab = rec["level"], rec["ab"]
                p_ab = 0.008 + (0.06 if ab < 9 and s["eng"] < -0.6 else 0)
                r = rng.random()
                if r < p_ab:
                    rec["outcome"], s["status"] = "abandon", "abandon"; continue
                if r < p_ab + cfg["depart"]:
                    rec["outcome"], s["status"] = "parti", "parti"; continue
                if ab < 8.6 and rng.random() < .4:
                    rec["outcome"], nl = "redouble", li
                else:
                    rec["outcome"], nl = "admis", li + 1
                if nl >= len(levels):
                    s["status"] = "diplome"; continue
                s["records"][sy] = {"level": nl, "kind": "reinscription"}
                nxt.append(s)
            for li, base in enumerate(cfg["base_new"]):
                n = round(base * (1 + cfg["growth"]) ** yi * rng.uniform(.93, 1.07))
                for _ in range(n):
                    s = new_student(li, "nouvelle", sy)
                    s["records"][sy] = {"level": li, "kind": "nouvelle"}
                    population.append(s); nxt.append(s)
        # répartition dans les classes (déséquilibrée volontairement : 1ère classe plus chargée)
        by_lvl = defaultdict(list)
        for s in nxt:
            s["records"][sy]["ab"] = year_ability(s)
            by_lvl[s["records"][sy]["level"]].append(s)
        for li, lst in by_lvl.items():
            rng.shuffle(lst)
            k = len(classes_by_level[li])
            weights = [1] if k == 1 else [.56, .44] if k == 2 else [.4, .3, .3]
            cut, acc = [], 0
            for w in weights[:-1]:
                acc += w; cut.append(round(acc * len(lst)))
            for i, s in enumerate(lst):
                ci = sum(i >= c for c in cut)
                s["records"][sy]["class"] = classes_by_level[li][ci]
        active = nxt
    # départs en cours d'année et sous-groupe en décrochage (année en cours)
    for s in active:
        rec = s["records"][cur_start]
        rec["outcome"] = "en cours"
        if rng.random() < 0.02:
            rec["outcome"], s["status"], rec["left_mid"] = "parti", "parti", True
        rec["decline"] = rng.random() < 0.08 and s["status"] == "actif"

    # ---------------------------------------------------------------- familles et parents
    families = []
    for s in population:
        if families and rng.random() < 0.12:
            fam = rng.choice(families[-40:])
            s["last"] = fam["last"]
            fam["kids"].append(s)
        else:
            families.append({"last": s["last"], "kids": [s]})
    for fam in families:
        eng = sum(k["eng"] for k in fam["kids"]) / len(fam["kids"])
        r = rng.random()
        fam["read_prob"] = 0.0 if r < 0.06 + (0.08 if eng < -1 else 0) else 0.35 if r < 0.2 else 0.88
        r = rng.random()
        fam["pay"] = "bad" if r < 0.07 + (0.06 if eng < -1 else 0) else "late" if r < 0.27 else "good"
    # famille de démonstration : 2 enfants actifs
    demo_fam = next((f for f in families if sum(k["status"] == "actif" for k in f["kids"]) >= 2), families[0])
    for i, fam in enumerate(families):
        demo = fam is demo_fam
        g = rng.random() < .5
        name = f"{'Mme' if g else 'M.'} {rng.choice(FIRST_F if g else FIRST_M)} {fam['last']}"
        email = f"parent@{code}.demo" if demo else f"parent.{i + 1}@{code}.demo"
        fam["user"] = mk_user("parent", email, name, demo, tel())
        if demo:
            fam["read_prob"], fam["pay"] = 0.9, "late"
    db.session.flush()

    # ---------------------------------------------------------------- élèves et inscriptions
    demo_student = None
    n_el = 0
    for fam in families:
        for s in fam["kids"]:
            last_sy = max(s["records"])
            st = Student(school_id=sid, first_name=s["first"], last_name=s["last"], gender=s["gender"],
                         birth_date=s["birth"], city=s["city"], status=s["status"],
                         parent_id=fam["user"].id, classroom_id=s["records"][last_sy]["class"].id)
            if s["status"] == "actif":
                demo = fam is demo_fam and demo_student is None
                n_el += 1
                email = f"eleve@{code}.demo" if demo else f"eleve.{n_el}@{code}.demo"
                u = mk_user("eleve", email, f"{s['first']} {s['last']}", demo)
                db.session.flush()
                st.user_id = u.id
                if demo:
                    demo_student = st
            db.session.add(st)
            s["obj"], s["fam"] = st, fam
    db.session.flush()
    year_by_start = {y.start_year: y for y in years}
    enr = []
    for s in population:
        for sy, rec in s["records"].items():
            enr.append(dict(school_id=sid, student_id=s["obj"].id, year_id=year_by_start[sy].id,
                            level_id=levels[rec["level"]].id, kind=rec["kind"], outcome=rec.get("outcome", "admis")))
    db.session.bulk_insert_mappings(Enrollment, enr)

    # ---------------------------------------------------------------- absences et notes
    absences, grades, abs_msgs = [], [], []
    for s in population:
        for sy, rec in s["records"].items():
            start, end = date(sy, 9, 15), date(sy + 1, 6, 30)
            frac = 1.0
            if sy == cur_start:
                end = today - timedelta(days=1)
                frac = max(0.05, (end - start).days / (date(sy + 1, 6, 30) - start).days)
                if rec.get("left_mid"):
                    end = min(end, date(sy, 10, 20))
            lam = 6.5 * math.exp(-0.55 * s["eng"]) * frac
            if rec.get("decline"):
                lam = lam * 2.5 + 3
            n_abs = poisson(rng, lam)
            p_just = max(.1, min(.9, .55 + .15 * s["eng"] - (.3 if rec.get("decline") else 0)))
            for d in sorted(set(weekdays_between(rng, start, end, n_abs))):
                just = rng.random() < p_just
                absences.append(dict(school_id=sid, student_id=s["obj"].id, on_date=d, kind="absence", justified=just,
                                     reason=rng.choice(["Maladie", "Rendez-vous médical", "Raison familiale"]) if just else None))
                if sy == cur_start and s["status"] == "actif":
                    abs_msgs.append((s, d))
            for d in sorted(set(weekdays_between(rng, start, end, poisson(rng, 4 * frac * math.exp(-0.4 * s["eng"]))))):
                absences.append(dict(school_id=sid, student_id=s["obj"].id, on_date=d, kind="retard", justified=False))
            terms = [1] if sy == cur_start else [1, 2, 3]
            trend = rng.gauss(0, .5)
            for t in terms:
                for sub in subjects:
                    sc = (rec["ab"] + SUBJECT_OFFSET.get(sub.name, 0) + trend * (t - 2) + rng.gauss(0, 1.6)
                          - min(2.5, 0.12 * n_abs / frac))
                    if rec.get("decline"):
                        sc -= 2.2
                    grades.append(dict(school_id=sid, student_id=s["obj"].id, subject_id=sub.id,
                                       classroom_id=rec["class"].id, year_id=year_by_start[sy].id, term=t,
                                       score=round(max(0, min(20, sc)) * 4) / 4))
    db.session.bulk_insert_mappings(Absence, absences)
    db.session.bulk_insert_mappings(Grade, grades)

    # ---------------------------------------------------------------- frais et paiements
    methods = (["Espèces", "Chèque", "Virement", "Carte"], [45, 20, 25, 10])
    fee_objs = []
    for s in population:
        for sy, rec in s["records"].items():
            amount = levels[rec["level"]].annual_fee / 10
            for i in range(10):
                m = (9 + i - 1) % 12 + 1
                y = sy + (1 if m < 9 else 0)
                due = date(y, m, 5)
                if rec.get("left_mid") and due > date(sy, 10, 20):
                    break
                f = Fee(school_id=sid, student_id=s["obj"].id, year_id=year_by_start[sy].id,
                        label=f"Mensualité {m:02d}/{y}", amount=amount, due_date=due, paid_amount=0)
                pay = s["fam"]["pay"]
                if pay == "good":
                    pd = due + timedelta(days=rng.randint(-7, 6))
                elif pay == "late":
                    pd = due + timedelta(days=rng.randint(8, 40))
                else:
                    pd = due + timedelta(days=rng.randint(5, 70)) if rng.random() < .55 else None
                if sy < cur_start - 0 and pd is None and rng.random() < .55:
                    pd = date(sy + 1, 7, rng.randint(1, 28))  # régularisation en fin d'année
                if pd and pd < today:
                    part = sy == cur_start and rng.random() < .05
                    f.paid_amount = amount / 2 if part else amount
                    f._pay = (pd, f.paid_amount, rng.choices(*methods)[0])
                fee_objs.append(f)
    db.session.add_all(fee_objs)
    db.session.flush()
    db.session.bulk_insert_mappings(Payment, [
        dict(school_id=sid, fee_id=f.id, student_id=f.student_id, paid_on=f._pay[0], amount=f._pay[1], method=f._pay[2])
        for f in fee_objs if hasattr(f, "_pay")])

    # ---------------------------------------------------------------- emploi du temps (année en cours)
    busy_t, busy_r, slots = set(), set(), []
    load = defaultdict(int)
    for ci, cls in enumerate(all_classes):
        home = rooms[ci]
        busy_c = set()
        per_day = defaultdict(int)
        subj_day = defaultdict(int)
        assigned = {}
        for sub in subjects:
            assigned[sub.id] = min(teachers_by_subj[sub.id], key=lambda t: load[t.id])
            load[assigned[sub.id].id] += sub.weekly_hours
        bad_balance = ci == 3  # une classe volontairement déséquilibrée
        hours = [sub for sub in subjects for _ in range(sub.weekly_hours)]
        rng.shuffle(hours)
        for sub in hours:
            days = sorted(range(5), key=lambda d: (d if bad_balance else per_day[d] + rng.random()))
            placed = False
            for d in days:
                if subj_day[(sub.id, d)] >= 2:
                    continue
                for h in rng.sample(HOURS, len(HOURS)):
                    if (d, h) in busy_c:
                        continue
                    cands = [assigned[sub.id]] + [t for t in teachers_by_subj[sub.id] if t is not assigned[sub.id]]
                    t = next((t for t in cands if (t.id, d, h) not in busy_t), None)
                    if not t:
                        continue
                    pref = lab if sub.name in ("Sciences", "Physique") else info if sub.name == "Informatique" else home
                    room = next((r for r in (pref, home, spare) if (r.id, d, h) not in busy_r), None)
                    if not room:
                        continue
                    busy_c.add((d, h)); busy_t.add((t.id, d, h)); busy_r.add((room.id, d, h))
                    per_day[d] += 1; subj_day[(sub.id, d)] += 1
                    slots.append(dict(school_id=sid, classroom_id=cls.id, subject_id=sub.id, teacher_id=t.id,
                                      room_id=room.id, day=d, hour=h))
                    placed = True
                    break
                if placed:
                    break
    # deux conflits injectés (à détecter par le tableau de bord n°8)
    if len(slots) > 40:
        a, b = slots[5], slots[40]
        slots.append(dict(a, classroom_id=b["classroom_id"], room_id=b["room_id"], day=a["day"], hour=a["hour"]))
        c = slots[60]
        slots.append(dict(c, teacher_id=all_teachers[-1].id, classroom_id=all_classes[-1].id, subject_id=c["subject_id"]))
    db.session.bulk_insert_mappings(TimetableSlot, slots)

    # ---------------------------------------------------------------- absences des enseignants
    tabs = []
    for sy in year_starts[-2:]:
        start = date(sy, 9, 15)
        end = today - timedelta(days=1) if sy == cur_start else date(sy + 1, 6, 30)
        n = 22 if sy == cur_start else 70
        for d in weekdays_between(rng, start, end, n):
            t = rng.choice(all_teachers)
            others = [o for o in teachers_by_subj[t.subject_id] if o is not t]
            rep = rng.choice(others).id if others and rng.random() < .7 else None
            tabs.append(dict(school_id=sid, teacher_id=t.id, on_date=d, hours=rng.choice([2, 3, 4, 6]),
                             replacement_id=rep))
    db.session.bulk_insert_mappings(TeacherAbsence, tabs)

    # ---------------------------------------------------------------- messages
    admin = User.query.filter_by(email=f"admin@{code}.demo").first()
    direct = User.query.filter_by(email=f"directeur@{code}.demo").first()
    now = datetime.combine(today, datetime.min.time()) + timedelta(hours=9)
    fam_by_year = defaultdict(set)
    for fi, fam in enumerate(families):
        for k in fam["kids"]:
            for sy in k["records"]:
                fam_by_year[sy].add(fi)
    fam_index = dict(enumerate(families))
    msgs = []

    def read_time(fam, sent):
        if rng.random() < fam["read_prob"]:
            rt = sent + timedelta(hours=rng.expovariate(1 / 30))
            return rt if rt < now else None
        return None

    d = today - timedelta(days=420)
    while d < today:
        for _ in range(2):
            sent = datetime.combine(d, datetime.min.time()) + timedelta(hours=rng.randint(8, 17), days=rng.randint(0, 13))
            if sent >= now:
                continue
            sy = sent.year if sent.month >= 9 else sent.year - 1
            subj = rng.choice(NOTICES)
            for fid in sorted(fam_by_year.get(sy, ())):
                fam = fam_index[fid]
                msgs.append(dict(school_id=sid, sender_id=rng.choice([admin.id, direct.id]),
                                 recipient_id=fam["user"].id, subject=subj,
                                 body=f"Chers parents,\n\n{subj} : merci de consulter les détails auprès de "
                                      f"l'administration.\n\nLa direction",
                                 sent_at=sent, read_at=read_time(fam, sent)))
        d += timedelta(days=30)
    for s, dd in abs_msgs:
        sent = datetime.combine(dd, datetime.min.time()) + timedelta(hours=11)
        msgs.append(dict(school_id=sid, sender_id=admin.id, recipient_id=s["fam"]["user"].id,
                         subject=f"Absence de {s['first']} {s['last']}",
                         body=f"Nous vous informons que {s['first']} a été absent(e) le {dd:%d/%m/%Y}.",
                         sent_at=sent, read_at=read_time(s["fam"], sent)))
    db.session.bulk_insert_mappings(Message, msgs)

    # ---------------------------------------------------------------- réclamations
    comps = []
    act_fams = [f for f in families if any(k["status"] == "actif" for k in f["kids"])]
    for _ in range(38 if code == "ibnkhaldoun" else 22):
        cat = rng.choices(list(COMPLAINT_TOPICS), [25, 20, 12, 10, 10, 15])[0]
        created = now - timedelta(days=rng.randint(1, 400), hours=rng.randint(0, 8))
        age = (now - created).days
        status = ("résolue" if rng.random() < .85 else "en cours") if age > 30 else rng.choice(["ouverte", "en cours", "résolue"])
        comps.append(dict(school_id=sid, parent_id=rng.choice(act_fams)["user"].id, category=cat,
                          subject=rng.choice(COMPLAINT_TOPICS[cat]), body="Merci de bien vouloir traiter ma demande.",
                          status=status, created_at=created,
                          resolved_at=created + timedelta(days=rng.randint(1, 15)) if status == "résolue" else None))
    db.session.bulk_insert_mappings(Complaint, comps)

    # ---------------------------------------------------------------- dépenses mensuelles
    exps = []
    for yi, sy in enumerate(year_starts):
        k = (1.045 ** yi)
        for i in range(12):
            m = (9 + i - 1) % 12 + 1
            y = sy + (1 if m < 9 else 0)
            dd = date(y, m, 28)
            if dd >= today:
                break
            exps += [
                dict(school_id=sid, category="Salaires", amount=round((len(all_teachers) * cfg["salary"] + cfg["staff"]) * k), spent_on=dd),
                dict(school_id=sid, category="Énergie et eau", amount=round(rng.uniform(3500, 6500) * k), spent_on=dd),
                dict(school_id=sid, category="Maintenance", amount=round(rng.uniform(1500, 9000) * k), spent_on=dd),
                dict(school_id=sid, category="Fournitures", amount=round(rng.uniform(1000, 4000) * k), spent_on=dd),
            ]
    db.session.bulk_insert_mappings(Expense, exps)
    db.session.commit()
    return school


def run(if_empty=False):
    app = create_app()
    with app.app_context():
        if if_empty and School.query.first():
            print("Base déjà initialisée – aucune action.")
            return
        db.drop_all()
        db.create_all()
        today = date.today()
        cur_start = today.year if today.month >= 9 else today.year - 1
        hashes = {r: generate_password_hash(p) for r, p in DEMO_PASSWORDS.items()}
        hashes["shared"] = generate_password_hash(SHARED_PASSWORD)
        db.session.add(User(school_id=None, email="superadmin@edusaas.demo", full_name="Super Administrateur",
                            role="superadmin", password_hash=hashes["superadmin"]))
        for cfg in SCHOOLS:
            # générateur propre à chaque école : noms et effectifs stables quelle que soit la date
            s = seed_school(cfg, random.Random(f"edusaas-{cfg['code']}"), hashes, today, cur_start)
            print(f"✔ {s.name} : {Student.query.filter_by(school_id=s.id, status='actif').count()} élèves actifs")
        db.session.commit()
        print("Données de démonstration générées.")


if __name__ == "__main__":
    run(if_empty="--if-empty" in sys.argv)
