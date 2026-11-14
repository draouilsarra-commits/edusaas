# EduSaaS – Application SaaS de gestion scolaire avec module BI & Aide à la Décision

Projet pratique **Cloud et Big Data** : application web multi-utilisateur et multi-école,
déployée sur le Cloud, qui intègre un Système d'Information Décisionnel (SID) de **10 tableaux de bord**.

## 1. Fonctionnalités

| Domaine | Fonctionnalités |
|---|---|
| **SaaS multi-école** | Console super-administrateur, création d'établissements (tenants), isolation stricte des données par `school_id` |
| **Rôles (RBAC)** | Administrateur, Directeur, Enseignant, Comptable, Parent, Élève, chacun avec des droits spécifiques |
| **Gestion** | Élèves (inscription + échéancier automatique), enseignants, classes, salles, utilisateurs |
| **Emplois du temps** | Grille par classe ou enseignant, contrôle automatique des conflits (classe / enseignant / salle) |
| **Paiements** | 10 mensualités par élève, encaissement, suivi des retards |
| **Évaluations** | Saisie des notes par trimestre, bulletins pondérés par coefficients, rang, mention, impression PDF |
| **Absences** | Appel en ligne, justification, notification automatique des parents |
| **Communication** | Messagerie (individuelle, par classe, tous les parents), accusés de lecture, réclamations avec suivi de statut |
| **BI & Aide à la décision** | 10 tableaux de bord, alertes, Early Warning System, prévisions, simulation de scénarios, recommandations automatiques, export CSV pour Power BI |

### Les 10 tableaux de bord
1. **Vue globale** : effectifs, répartition par niveau, évolution des inscriptions, effectif vs capacité, alertes classes surchargées / sous-remplies.
2. **Financier** : frais attendus, encaissé, reste à payer, taux de recouvrement, parents en retard, recettes mensuelles + **prévision 3 mois**.
3. **Pédagogique** : moyennes par classe / niveau / matière, taux de réussite, évolution entre trimestres, élèves à accompagner.
4. **Absences** : taux d'absentéisme, justifiées / non justifiées, absences répétitives, évolution mensuelle, **corrélation de Pearson absences ↔ résultats**.
5. **Enseignants** : effectifs par matière et niveau, charge horaire vs contrat, surcharge / sous-charge, absences et remplacements.
6. **Inscriptions & fidélisation** : nouvelles inscriptions, réinscriptions, taux de renouvellement, départs / abandons, origine géographique, **prévision par cohortes**.
7. **Communications** : messages envoyés, taux de lecture, parents non lecteurs, réclamations, situations nécessitant un renforcement du suivi parental.
8. **Emplois du temps & ressources** : occupation des salles, conflits d'horaires, salles sous-utilisées, équilibre des emplois du temps.
9. **Early Warning System** : score de risque explicable (0-100), risque d'échec, d'abandon, signal combiné absences + baisse, risque d'impayés, action recommandée.
10. **Stratégique** : prévisions inscriptions / recettes / dépenses, **simulateur interactif de scénarios**, besoins en classes / enseignants / salles, comparaison pluriannuelle, recommandations automatiques.

## 2. Architecture

```
Navigateur ──HTTPS──▶ Render (PaaS, conteneur Docker)
                        │  Gunicorn (WSGI) → Flask
                        │   ├─ auth / RBAC / CSRF
                        │   ├─ modules de gestion (main.py)
                        │   ├─ couche analytique (analytics.py)
                        │   └─ 10 tableaux de bord (bi.py) → Chart.js
                        ▼
                 PostgreSQL managé (Neon, serverless)
                        │
                        └─ export CSV ──▶ Power BI / entrepôt de données
```

- **Modèle SaaS multi-tenant** : base partagée, schéma partagé, discriminant `school_id` sur chaque table ;
  toutes les requêtes passent par `scoped()` ; un accès à l'objet d'une autre école renvoie 404 (testé).
- **Cloud** : application *stateless* conteneurisée (12-factor : configuration par variables d'environnement
  `DATABASE_URL`, `SECRET_KEY`), base managée séparée du calcul → mise à l'échelle horizontale possible.
- **Données** : ~550 élèves actifs, 4 années d'historique, ~45 000 notes, ~22 000 échéances, ~21 000 absences
  ~15 000 messages, générés de façon déterministe (`app/seed.py`).
- **Sécurité** : mots de passe hachés (PBKDF2/scrypt Werkzeug), jeton CSRF, cookies HttpOnly, contrôle d'accès par rôle
  et par propriétaire (un parent ne voit que ses enfants, un enseignant que ses classes).

```
app/
  __init__.py     factory Flask, config, CSRF, erreurs
  models.py       modèle de données (16 tables)
  utils.py        RBAC, isolation tenant
  auth.py         connexion, mot de passe
  platform.py     console super-admin (tenants)
  main.py         modules de gestion
  services.py     moyennes, bulletins
  analytics.py    agrégations, scoring de risque, prévisions
  bi.py           10 tableaux de bord + export CSV
  seed.py         données de démonstration
  templates/      vues Jinja2   static/  CSS + Chart.js
tests/test_smoke.py  tests de tous les profils, RBAC et isolation
```

## 3. Lancer en local

```bash
pip install -r requirements.txt
python -m app.seed          # crée la base SQLite + données de démo
flask --app app run         # http://127.0.0.1:5000
python tests/test_smoke.py  # tests
```

## 4. Déploiement Cloud (gratuit) : Render + Neon

> Pourquoi Neon ? La base PostgreSQL gratuite de Render expire 30 jours après sa création, ce qui est trop court
> pour la période de correction. Neon propose une base PostgreSQL gratuite sans expiration.

1. **Base de données** : créez un compte sur neon.tech → *New project* → copiez la *connection string*
   (`postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).
2. **Code** : poussez ce dossier sur un dépôt GitHub.
3. **Render** : render.com → *New* → *Blueprint* (ou *Web Service* → *Docker*) → sélectionnez le dépôt.
   Le fichier `render.yaml` est détecté.
4. Dans *Environment*, renseignez `DATABASE_URL` = la chaîne Neon (`SECRET_KEY` est générée automatiquement).
5. Déployez. Au premier démarrage, `python -m app.seed --if-empty` remplit la base, puis Gunicorn démarre.
6. L'URL publique est du type `https://edusaas-xxxx.onrender.com` : reportez-la dans le document Word.

Le service gratuit de Render se met en veille après 15 min d'inactivité : la première visite peut prendre
environ une minute. Ouvrez l'URL juste avant la correction ou une démonstration.

Autres options : Railway, Fly.io, Azure App Service, AWS Elastic Beanstalk ou Google Cloud Run
(même conteneur Docker, même variable `DATABASE_URL`).

## 5. Comptes de démonstration

Voir le document Word `EduSaaS_Identifiants_connexion.docx`. Format : `<role>@<ecole>.demo`
(écoles : `ibnkhaldoun`, `alamal`). Tous les comptes sont fictifs, avec des mots de passe temporaires.
