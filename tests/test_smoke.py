"""Tests de fumée : connexion de chaque profil, accès aux pages, RBAC et isolation.

Lancer :  python -m app.seed && python tests/test_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app, db  # noqa: E402
from app.models import Fee, Student, User  # noqa: E402
from app.seed import DEMO_PASSWORDS  # noqa: E402

app = create_app({"TESTING_NO_CSRF": True})
PAGES = {
    "admin": ["/", "/eleves", "/eleves/nouveau", "/enseignants", "/classes", "/utilisateurs", "/emploi-du-temps",
              "/paiements", "/paiements?filtre=tout", "/notes", "/absences", "/bulletins", "/messages", "/reclamations"]
             + [f"/bi/{k}" for k in ["general", "financier", "pedagogique", "absences", "enseignants", "inscriptions",
                                     "communications", "ressources", "risques", "strategique"]]
             + ["/bi/pedagogique?trimestre=1", "/bi/export/notes.csv", "/bi/export/eleves.csv"],
    "directeur": ["/bi/general", "/bi/risques", "/bi/strategique", "/eleves", "/enseignants", "/absences"],
    "comptable": ["/", "/bi/financier", "/paiements", "/eleves"],
    "enseignant": ["/", "/notes", "/absences", "/emploi-du-temps", "/eleves", "/messages", "/bulletins"],
    "parent": ["/", "/emploi-du-temps", "/messages", "/reclamations"],
    "eleve": ["/", "/emploi-du-temps", "/messages"],
}
FORBIDDEN = {
    "comptable": ["/bi/risques", "/utilisateurs", "/notes"],
    "enseignant": ["/bi/general", "/paiements", "/utilisateurs"],
    "parent": ["/bi/financier", "/eleves", "/paiements", "/notes"],
    "eleve": ["/bi/general", "/reclamations", "/paiements"],
    "directeur": ["/utilisateurs", "/notes"],
}
errors = []


def login(c, email, pwd):
    r = c.post("/login", data={"email": email, "password": pwd})
    assert r.status_code == 302, f"login failed {email}"


for code in ["ibnkhaldoun", "alamal"]:
    for role, pages in PAGES.items():
        c = app.test_client()
        login(c, f"{'prof' if role == 'enseignant' else role}@{code}.demo", DEMO_PASSWORDS[role])
        for p in pages:
            r = c.get(p)
            if r.status_code not in (200, 302):
                errors.append(f"{code} {role} {p} -> {r.status_code}")
        for p in FORBIDDEN.get(role, []):
            r = c.get(p)
            if r.status_code != 403:
                errors.append(f"RBAC {code} {role} {p} -> {r.status_code} (403 attendu)")

with app.app_context():
    u = User.query.filter_by(email="parent@ibnkhaldoun.demo").first()
    kids = Student.query.filter_by(parent_id=u.id).all()
    other_school_student = Student.query.filter(Student.school_id != u.school_id).first()
    other_kid = Student.query.filter(Student.school_id == u.school_id, Student.parent_id != u.id).first()
    fee = Fee.query.filter(Fee.school_id != u.school_id).first()
    kid_ids = [k.id for k in kids]
    oss, ok, fid = other_school_student.id, other_kid.id, fee.id

c = app.test_client()
login(c, "parent@ibnkhaldoun.demo", DEMO_PASSWORDS["parent"])
for k in kid_ids:
    for p in [f"/eleves/{k}", f"/bulletin/{k}?trimestre=1"]:
        if c.get(p).status_code != 200:
            errors.append(f"parent cannot see own child {p}")
if c.get(f"/eleves/{ok}").status_code != 403:
    errors.append("parent can see another family's child")
if c.get(f"/eleves/{oss}").status_code != 404:
    errors.append("ISOLATION: parent can see another school's student")

c = app.test_client()
login(c, "comptable@ibnkhaldoun.demo", DEMO_PASSWORDS["comptable"])
if c.post(f"/paiements/{fid}/encaisser", data={"amount": "10"}).status_code != 404:
    errors.append("ISOLATION: accountant can pay another school's fee")

c = app.test_client()
login(c, "superadmin@edusaas.demo", DEMO_PASSWORDS["superadmin"])
if c.get("/plateforme/").status_code != 200:
    errors.append("superadmin console")
if c.get("/bi/general").status_code != 403:
    errors.append("superadmin should not access school BI")

# écriture : saisie de note, appel, message, réclamation
c = app.test_client()
login(c, "prof@alamal.demo", DEMO_PASSWORDS["enseignant"])
r = c.get("/notes")
assert r.status_code == 200
r = c.post("/absences", data={})
if r.status_code not in (302,):
    errors.append(f"teacher absence post -> {r.status_code}")

c = app.test_client()
login(c, "parent@alamal.demo", DEMO_PASSWORDS["parent"])
r = c.post("/reclamations", data={"category": "Autre", "subject": "Test", "body": "Test"})
if r.status_code != 302:
    errors.append("complaint post")

c = app.test_client()
r = c.post("/login", data={"email": "admin@alamal.demo", "password": "wrong"})
if b"incorrects" not in r.data:
    errors.append("bad password accepted?")

print("\n".join(errors) if errors else "✅ Tous les tests de fumée passent.")
sys.exit(1 if errors else 0)
