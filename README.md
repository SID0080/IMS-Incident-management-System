# Incident Management System

An incident management platform for airport IT operations, built as a summer internship project at **Lucknow International Airport Limited (Adani Group)**.

The system replaces an informal, untracked reporting process with a structured pipeline: incidents are reported through a guided web form, classified automatically by a dual-engine machine learning pipeline, routed to a support engineer of the correct tier, monitored against response-time guarantees, and recorded in an append-only audit trail.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Machine Learning Pipeline](#machine-learning-pipeline)
- [Routing Model](#routing-model)
- [Approval-Gated Hold Workflow](#approval-gated-hold-workflow)
- [SLA Enforcement and Accountability](#sla-enforcement-and-accountability)
- [Technology Stack](#technology-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [API Overview](#api-overview)
- [Future Scope](#future-scope)
- [Acknowledgements](#acknowledgements)

---

## Overview

Lucknow International Airport operates around the clock across multiple terminals, with equipment ranging from check-in desktops and boarding-pass printers to X-ray baggage scanners, CCTV networks, and access-control gates. Before this project, equipment faults were reported by phone, messaging apps, or in person, with no record of when an incident was raised, no guarantee of response time, no visibility for management, and no way to establish responsibility for a delayed resolution.

The system addresses four gaps:

1. **Intake** - a single structured reporting channel with airport-specific device catalogues.
2. **Routing** - automatic department classification and tier-aware assignment, so the reporter never needs to know the organisational chart.
3. **Urgency** - explicit, priority-dependent response windows enforced by an autonomous background engine.
4. **Accountability** - penalty points, automatic blacklisting, and an audit trail that records who decided what and when.

---

## Key Features

**Guided incident reporting.** A sixteen-entry device catalogue with dependent problem dropdowns that auto-fill classifier-friendly titles. Optional photographic evidence, terminal and location capture. Equipment QR codes can pre-fill the form via URL parameters, leaving priority assessment to the reporter on the scene.

**Dual-engine ML classification.** A custom-trained text classifier and a general-purpose image classifier vote independently on the responsible department, merged by a reliability-weighted combiner that escalates to human triage rather than guessing when confidence is low.

**Tier-based routing (L1/L2/L3).** Incidents route directly to an engineer of the appropriate seniority based on priority, with least-busy selection within a tier and fallback chains between tiers.

**Approval-gated hold workflow.** When urgent work displaces routine work, a worker must request a hold naming the blocking incident and a written justification; a manager must approve; the worker must then execute. The SLA clock stops only at execution, and resumes automatically with the exact saved remaining time once the blocking incident is resolved.

**Approval-gated delegation.** On high and critical incidents, a lead engineer may request a named helper. Once a manager approves, the helper is attached as an additional assignee and shares the SLA consequences.

**Autonomous SLA enforcement.** A scheduler job runs every minute, escalating overdue incidents, deducting penalty points from every assignee, incrementing permanent breach counters, and notifying managers by email. Held incidents are exempt.

**Accountability model.** Per-assignee penalty points, automatic removal from the routing pool at a configurable threshold, permanent breach history, and per-worker performance analytics with exportable reports.

**Management analytics.** Live SLA dashboard, department breach rates, incident breakdowns by status, priority, and department, with print-quality PDF exports drawn from data rather than screenshots.

**Emergency channel.** A dedicated reporting path that bypasses classification entirely and alerts all managers immediately.

**Transactional email.** Fourteen HTML templates covering assignment, escalation, hold request, approval and denial, delegation, blacklisting, and emergency alerts.

---

## Architecture

The system follows a conventional three-tier architecture with one deliberate simplification: the frontend is a set of static HTML pages served without any build step, so the entire interface can be maintained with a text editor.

```
+---------------------------------------------------------------+
|  Presentation Tier                                            |
|  Static HTML pages, Tailwind CSS, vanilla JavaScript          |
|  Client auth guards, role gates, per-tab session isolation    |
+---------------------------------------------------------------+
                              |  REST / JSON
                              v
+---------------------------------------------------------------+
|  Application Tier                                             |
|  FastAPI, asynchronous SQLAlchemy 2.0, JWT authentication     |
|  ML models held in memory, APScheduler SLA engine, SMTP       |
+---------------------------------------------------------------+
                              |
                              v
+---------------------------------------------------------------+
|  Data Tier                                                    |
|  PostgreSQL: users, tickets, hold_requests, help_requests,    |
|  registration_codes, append-only audit_logs                   |
+---------------------------------------------------------------+
```

Authentication uses JSON Web Tokens validated server-side on every request; the API is the security boundary. Staff accounts additionally require a personal, email-bound access code, pre-provisioned by an administrator, at registration and at every login.

Incidents are keyed internally by UUID but exposed everywhere as an eight-digit ticket number generated from a dedicated PostgreSQL sequence, so concurrent creation can never produce a duplicate.

---

## Machine Learning Pipeline

Two independent engines classify each incident into one of six departments: IT, Security, HR, Technical Operations, Marketing, and Legal.

**Text engine.** A scikit-learn pipeline of TF-IDF vectorisation followed by Logistic Regression, trained on incident-style text. The corpus was extended with real helpdesk phrasing so that the vocabulary produced by the report form's dropdowns falls inside the model's strongest regions - guided input raises confidence at no model cost. Predictions below `0.60` confidence return no category rather than a weak guess.

**Image engine.** MobileNetV2 with ImageNet weights. The top predicted labels are matched against a hand-built keyword map to departments. Because ImageNet contains no airport-equipment classes, this engine is inherently noisier than the text classifier and is treated accordingly.

**Reliability-weighted combiner.**

| Condition | Behaviour |
|---|---|
| Both engines agree | Confidence blends 75 percent text, 25 percent image, plus a small agreement bonus |
| Engines disagree | Text wins by default; image overrides only at 1.5x or greater the text confidence |
| Image confidence below 0.35 | Image prediction discarded entirely |
| Neither engine confident | Incident stored uncategorised, managers alerted, one-click manual categorisation re-triggers routing |

Every combination decision - which engine won, at what confidence, by which rule - is written to the audit log, making classification behaviour fully inspectable after the fact.

The 0.35 image threshold and the 1.5x override margin were introduced after a test case in which a CCTV camera photograph matched loudspeaker-adjacent ImageNet labels and pulled a clearly-IT incident toward Marketing at 26 percent confidence.

---

## Routing Model

| Priority | SLA Window | Preferred Tier | Fallback Order |
|---|---|---|---|
| Low | 24 hours | L1 | L2, L3, any |
| Medium | 8 hours | L1 | L2, L3, any |
| High | 2 hours | L2 | L3, L1, any |
| Critical | 30 minutes | L3 | L2, L1, any |

Within a tier, the least-busy eligible engineer wins. Load is computed as combined duty - incidents led plus incidents helped on - so an engineer assisting on a critical incident never appears free. A per-worker load cap prevents pile-ups and is deliberately ignored for high and critical incidents, where urgency outweighs balance. Blacklisted and deactivated workers are excluded at the query level.

---

## Approval-Gated Hold Workflow

An earlier iteration paused a worker's lower-priority incident automatically the moment a higher-priority one arrived. It worked, but the pause was silent: the engineer never chose to deprioritise, yet their incident froze. The redesigned workflow makes preemption a deliberate, approved, recorded decision.

```
PENDING  ->  APPROVED  ->  EXECUTED  ->  COMPLETED
   |
   +-------->  DENIED
```

1. **Request.** A worker holding two active incidents requests a hold on the lower-priority one, naming the strictly-higher-priority blocking incident and writing a mandatory justification. Both SLA clocks continue running.
2. **Approve.** A manager reviews the justification. Approval unlocks the Hold button; it does not pause anything.
3. **Execute.** The worker executes the hold. Only now does the incident pause, with its remaining SLA seconds saved to the database.
4. **Auto-resume.** When the blocking incident is resolved, every incident held because of it resumes automatically with exactly the seconds saved at pause.

Design properties worth noting: nothing pauses without two human decisions; resumption is tied to the specific blocking incident rather than to whatever the worker happened to have paused; and a worker who never requests a hold, or whose request is denied, and who then breaches, takes the penalty - with the audit trail showing exactly what was asked and what was answered.

---

## SLA Enforcement and Accountability

A scheduler job examines all in-progress, non-paused incidents past their deadline every minute. Each breach escalates the incident to a critical review state, records an escalation event in the audit log, deducts penalty points from every assignee, increments permanent breach counters, and emails the department manager and administrators.

| Breached Priority | Penalty per Assignee |
|---|---|
| Critical | 15 points |
| High | 10 points |
| Medium | 5 points |
| Low | 2 points |

At 30 accumulated points a worker is automatically removed from the routing pool until an administrator reviews and clears the flag. Points reset on clearance; the breach counter is permanent by design. The threshold corresponds to two critical breaches, three high, or six medium, chosen so that any single failure is recoverable but a pattern triggers human intervention.

---

## Technology Stack

| Layer | Technologies |
|---|---|
| Backend | FastAPI, Python 3.10+, asynchronous SQLAlchemy 2.0, Pydantic v2 |
| Database | PostgreSQL, UUID primary keys, atomic sequence for ticket numbers, append-only audit log |
| Machine learning | scikit-learn (TF-IDF, Logistic Regression), TensorFlow (MobileNetV2), joblib |
| Scheduling | APScheduler |
| Email | smtplib with HTML templates |
| Frontend | Vanilla JavaScript, Tailwind CSS, Chart.js, jsPDF, no build step |
| Security | JWT authentication, email-bound access codes, role-gated pages, per-tab session isolation |

---

## Getting Started

### Prerequisites

- Python 3.10 or later
- PostgreSQL 14 or later
- An SMTP account for email notifications (optional for local use)

The trained text classifier ships with this repository, so no model training is required to run the application. MobileNetV2 weights are downloaded automatically by Keras on first use.

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

### 2. Create the database

Connect to PostgreSQL as a superuser and run:

```sql
CREATE DATABASE ims_db;
CREATE USER ims_user WITH PASSWORD 'your-password';
GRANT ALL PRIVILEGES ON DATABASE ims_db TO ims_user;
```

Then, connected to `ims_db`:

```sql
GRANT ALL ON SCHEMA public TO ims_user;
ALTER SCHEMA public OWNER TO ims_user;
```

The schema ownership step is required on PostgreSQL 15 and later, where default privileges on the public schema are revoked from non-owner roles.

### 3. Create the ticket number sequence

Ticket numbers are generated from a dedicated sequence rather than by the ORM, so it must be created explicitly:

```sql
CREATE SEQUENCE ticket_number_seq START WITH 10000001;
GRANT USAGE, SELECT ON SEQUENCE ticket_number_seq TO ims_user;
```

### 4. Set up the Python environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Configure the environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum `DATABASE_URL` and `SECRET_KEY`. See [Configuration](#configuration) for the full list.

### 6. Start the backend

```bash
uvicorn app.main:app --port 8000
```

On first start the application creates all tables and seeds a default administrator. Verify the service:

```
http://localhost:8000/health
http://localhost:8000/docs
```

### 7. Seed demonstration users

```bash
python scripts/seed_users.py
```

This creates one manager and five workers per department, with engineer tiers assigned as L1, L1, L2, L2, L3 within each department.

### 8. Serve the frontend

```bash
cd frontend
python -m http.server 5500
```

Open `http://localhost:5500/basic.html`.

### Default credentials

The application seeds a single administrator on first run. **Change this password before any non-local deployment.**

| Role | Email | Password |
|---|---|---|
| Administrator | admin@adani.com | admin123 |

Seeded managers and workers use the passwords defined in `scripts/seed_users.py` and additionally require their personal access code at login.

### Retraining the classifier (optional)

The bundled model is ready to use. To retrain or extend it:

```bash
python scripts/extend_it_training_data.py
python scripts/train_categorizer.py
```

This regenerates `app/ml/models/categorizer.joblib`.

---

## Configuration

All configuration is read from a `.env` file in the project root. A template is provided as `.env.example`.

| Variable | Description |
|---|---|
| `DATABASE_URL` | Async PostgreSQL connection string. Special characters in the password must be URL-encoded. |
| `SECRET_KEY` | Secret used to sign JWTs. Must be replaced with a random value in production. |
| `ALGORITHM` | JWT signing algorithm. Default `HS256`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime in minutes. |
| `DEBUG` | Enables verbose logging. |
| `APP_NAME`, `VERSION` | Displayed in the API documentation and health endpoint. |
| `ML_MODEL_PATH` | Path to the serialised text classifier. |
| `TF_MODEL_PATH` | Path to the image classifier. |
| `ML_CONFIDENCE_THRESHOLD` | Minimum text-classifier confidence required to accept a prediction. |
| `SLA_LOW_RESPONSE_HRS` | Response window for low-priority incidents. |
| `SLA_MEDIUM_RESPONSE_HRS` | Response window for medium-priority incidents. |
| `SLA_HIGH_RESPONSE_HRS` | Response window for high-priority incidents. |
| `SLA_CRITICAL_RESPONSE_HRS` | Response window for critical incidents. |
| `SMTP_HOST`, `SMTP_PORT` | Mail server host and port. |
| `SMTP_USER`, `SMTP_PASSWORD` | Mail account credentials. For Gmail, use an App Password. |
| `ALERT_EMAIL_FROM` | From address on outgoing notifications. |

The `.env` file is excluded from version control. Never commit real credentials.

Tuning constants currently defined in code rather than configuration include the image-classifier threshold, the image override margin, per-priority penalty points, the blacklist threshold, and the maximum worker load.

---

## Project Structure

```
.
├── app/
│   ├── core/               Security, dependencies, accountability rules
│   ├── ml/models/          Trained text classifier
│   ├── models/             SQLAlchemy ORM models
│   ├── routers/            API route modules grouped by actor
│   ├── schemas/            Pydantic request and response schemas
│   ├── services/           Ticketing, routing, ML, SLA, email logic
│   ├── config.py           Settings loaded from the environment
│   ├── database.py         Async engine and session factory
│   └── main.py             Application entry point and lifespan
├── frontend/               Static HTML pages
├── scripts/                Seeding and model-training utilities
├── uploads/                Uploaded incident images (not versioned)
├── .env.example
├── requirements.txt
└── README.md
```

---

## API Overview

Interactive OpenAPI documentation is generated automatically and available at `/docs` when the application is running. Routes are organised by actor:

| Prefix | Purpose |
|---|---|
| `/auth` | Registration and login |
| `/tickets` | Incident creation and queries |
| `/worker` | Status transitions, help requests, hold lifecycle, personal performance |
| `/admin` | Triage, manual assignment, approvals, accountability, engineer tiers, registration codes, analytics |
| `/sla` | Live SLA status and dashboards |
| `/analytics` | Aggregate reporting |
| `/emergency` | Emergency escalation, bypassing classification |

Role enforcement is implemented in reusable dependencies, so no endpoint can omit its access check.

---

## Future Scope

- Fine-tune the image model on photographs of the airport's own equipment, allowing a larger ensemble weight for the image engine.
- Move email delivery from a personal SMTP account to a corporate service account or a transactional provider.
- Move ML inference to a task queue and add read replicas once concurrent load warrants it; the stateless API makes this incremental.
- Native mobile reporting with camera capture and push notifications.
- Breach-rate trend analytics by department and tier to inform staffing.
- A knowledge base that mines resolved incidents to suggest fixes on similar new reports.

---

## Acknowledgements

Developed during a summer internship in the IT Department of Lucknow International Airport Limited (Adani Group), under the guidance of **Mr. Chandra Prakash Mishra** and **Mr. Gaurav Dixit**.

**Author:** Kartikey
B.Tech Computer Science and Engineering, Thapar Institute of Engineering and Technology, Patiala

---

## License

This project was produced in an internship context. Confirm ownership and licensing terms with the host organisation before distributing or reusing the code.
