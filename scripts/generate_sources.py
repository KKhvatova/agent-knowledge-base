"""Generate the synthetic POC sources under data/sources/.

All synthetic content lives in this one file, so the facts used by the evaluation
suite have a single origin. The output imitates three different systems, each with
its own format and its own way of expressing permissions:

- confluence-hr/  Confluence-like export: one HTML body + one JSON metadata file per page.
                  Permissions are Confluence group names (e.g. "staff-it").
- drive-benefits/ Drive-like folder: PDF files + an index with Drive-style permissions
                  (group e-mail addresses, e.g. "staff-it@groups.example").
- hris/           HRIS JSON exports: employees, leave balances, leave policy table.

Deliberate test cases embedded in the data:
- CONFLICT: the US handbook says 20 PTO days, the HRIS policy table (more recent) says 15.
- FUTURE VALIDITY: Italy has a new smart-working policy valid from 2027-01-01.
- CONFIDENTIAL: the Italian disciplinary procedure is readable by HR only.

Everything here is SYNTHETIC. It is not a real ShopFully or Flipp policy.

Usage:  uv run python scripts/generate_sources.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1] / "data" / "sources"
DISCLAIMER = "SYNTHETIC DATA for a proof of concept. Not a real ShopFully or Flipp policy."

# --------------------------------------------------------------------------------------
# Confluence-like HR pages
# --------------------------------------------------------------------------------------
# Each page: id, title, space, language, labels, restrictions (Confluence groups),
# version number + timestamp, optional validity, and an HTML body.

PAGES: list[dict] = [
    # ---------------------------------------------------------------- Italy (Italian)
    {
        "id": "1001",
        "title": "Ferie e permessi – Italia",
        "space": "HR-IT",
        "language": "it",
        "labels": ["leave", "italy"],
        "read_groups": ["staff-it", "hr-it", "hr-global"],
        "version": 4,
        "updated": "2026-02-10T09:00:00Z",
        "body": """
<h1>Ferie e permessi – Italia</h1>
<h2>Giorni di ferie</h2>
<p>I dipendenti a tempo pieno maturano <strong>26 giorni lavorativi di ferie</strong> all'anno,
in proporzione ai mesi lavorati. Almeno 2 settimane vanno godute nell'anno di maturazione.</p>
<h2>Permessi (ROL)</h2>
<p>Oltre alle ferie spettano <strong>104 ore di permessi retribuiti (ROL)</strong> all'anno.</p>
<h2>Come richiedere le ferie</h2>
<ol>
<li>Inserisci la richiesta nel portale HR <em>Persona</em>.</li>
<li>Per periodi superiori a 5 giorni, invia la richiesta con almeno 15 giorni di anticipo.</li>
<li>Il tuo responsabile approva o rifiuta entro 3 giorni lavorativi.</li>
</ol>
<h2>Residui</h2>
<p>Le ferie non godute entro il 30 giugno dell'anno successivo vengono pianificate d'ufficio.</p>
""",
    },
    {
        "id": "1002",
        "title": "Malattia – Italia",
        "space": "HR-IT",
        "language": "it",
        "labels": ["sick-leave", "italy"],
        "read_groups": ["staff-it", "hr-it", "hr-global"],
        "version": 2,
        "updated": "2025-11-03T09:00:00Z",
        "body": """
<h1>Malattia – Italia</h1>
<h2>Comunicazione</h2>
<p>In caso di malattia avvisa il tuo responsabile e HR <strong>prima dell'inizio
dell'orario di lavoro</strong>.</p>
<h2>Certificato</h2>
<p>Il medico invia il certificato telematico all'INPS. Comunica a HR il
<strong>numero di protocollo</strong> del certificato entro 2 giorni.</p>
<h2>Trattamento economico</h2>
<p>L'azienda integra l'indennità INPS fino al <strong>100% della retribuzione</strong>
per i primi 180 giorni dell'anno.</p>
""",
    },
    {
        "id": "1003",
        "title": "Congedo parentale – Italia",
        "space": "HR-IT",
        "language": "it",
        "labels": ["parental-leave", "italy"],
        "read_groups": ["staff-it", "hr-it", "hr-global"],
        "version": 3,
        "updated": "2026-01-20T09:00:00Z",
        "body": """
<h1>Congedo parentale – Italia</h1>
<h2>Maternità</h2>
<p>Congedo obbligatorio di <strong>5 mesi</strong>; l'azienda integra l'indennità INPS
fino al 100% della retribuzione.</p>
<h2>Paternità</h2>
<p>Congedo obbligatorio di <strong>10 giorni lavorativi</strong>, più <strong>5 giorni
aggiuntivi retribuiti</strong> offerti dall'azienda.</p>
<h2>Come fare richiesta</h2>
<p>Invia la domanda all'INPS e carica la ricevuta nel portale <em>Persona</em> almeno
30 giorni prima dell'inizio del congedo.</p>
""",
    },
    {
        "id": "1004",
        "title": "Smart working – Italia",
        "space": "HR-IT",
        "language": "it",
        "labels": ["remote-work", "italy"],
        "read_groups": ["staff-it", "hr-it", "hr-global"],
        "version": 5,
        "updated": "2025-12-01T09:00:00Z",
        "valid_from": "2025-01-01",
        "valid_to": "2026-12-31",
        "body": """
<h1>Smart working – Italia</h1>
<h2>Giorni da remoto</h2>
<p>È possibile lavorare da remoto fino a <strong>2 giorni a settimana</strong>,
concordandoli con il proprio responsabile.</p>
<h2>Strumenti</h2>
<p>L'azienda fornisce laptop e un contributo una tantum di 300 euro per la postazione.</p>
""",
    },
    {
        "id": "1005",
        "title": "Smart working – Italia (dal 2027)",
        "space": "HR-IT",
        "language": "it",
        "labels": ["remote-work", "italy"],
        "read_groups": ["staff-it", "hr-it", "hr-global"],
        "version": 1,
        "updated": "2026-11-05T09:00:00Z",
        "valid_from": "2027-01-01",
        "replaces": "1004",
        "body": """
<h1>Smart working – Italia (dal 2027)</h1>
<p>Dal <strong>1° gennaio 2027</strong> sostituisce la policy precedente.</p>
<h2>Giorni da remoto</h2>
<p>È possibile lavorare da remoto fino a <strong>3 giorni a settimana</strong>,
concordandoli con il proprio responsabile.</p>
<h2>Strumenti</h2>
<p>L'azienda fornisce laptop e un contributo annuale di 200 euro per la postazione.</p>
""",
    },
    {
        "id": "1006",
        "title": "Procedura disciplinare – Italia",
        "space": "HR-IT",
        "language": "it",
        "labels": ["disciplinary", "italy", "confidential"],
        "read_groups": ["hr-it", "hr-global"],
        "classification": "confidential",
        "version": 2,
        "updated": "2026-04-15T09:00:00Z",
        "body": """
<h1>Procedura disciplinare – Italia</h1>
<p><strong>Documento riservato: solo per il personale HR.</strong></p>
<h2>Contestazione</h2>
<p>La contestazione dell'addebito va fatta per iscritto e consegnata al dipendente,
che ha <strong>5 giorni</strong> per presentare le proprie giustificazioni.</p>
<h2>Provvedimenti</h2>
<p>I provvedimenti possibili sono, in ordine di gravità: richiamo verbale, richiamo scritto,
multa, sospensione, licenziamento. Ogni provvedimento va approvato da HR Director.</p>
""",
    },
    # ---------------------------------------------------------------- Canada (English)
    {
        "id": "2001",
        "title": "Vacation and Time Off – Canada",
        "space": "HR-CA",
        "language": "en",
        "labels": ["leave", "canada"],
        "read_groups": ["staff-ca", "hr-ca", "hr-global"],
        "version": 6,
        "updated": "2026-01-15T14:00:00Z",
        "body": """
<h1>Vacation and Time Off – Canada</h1>
<h2>Entitlement</h2>
<p>Full-time employees receive <strong>15 days of paid vacation</strong> per calendar year,
accrued monthly.</p>
<h2>Carry-over</h2>
<p>Up to <strong>5 unused days</strong> can be carried over and must be used by March 31.</p>
<h2>How to request vacation</h2>
<ol>
<li>Submit the request in Workday at least 2 weeks in advance.</li>
<li>Your manager approves it within 3 business days.</li>
</ol>
""",
    },
    {
        "id": "2002",
        "title": "Sick Leave – Canada",
        "space": "HR-CA",
        "language": "en",
        "labels": ["sick-leave", "canada"],
        "read_groups": ["staff-ca", "hr-ca", "hr-global"],
        "version": 2,
        "updated": "2025-09-10T14:00:00Z",
        "body": """
<h1>Sick Leave – Canada</h1>
<p>Employees have <strong>5 paid sick days</strong> per year. Notify your manager before
the start of your working day. A medical note is required after 3 consecutive days.</p>
""",
    },
    {
        "id": "2003",
        "title": "Parental Leave – Canada",
        "space": "HR-CA",
        "language": "en",
        "labels": ["parental-leave", "canada"],
        "read_groups": ["staff-ca", "hr-ca", "hr-global"],
        "version": 3,
        "updated": "2026-03-01T14:00:00Z",
        "body": """
<h1>Parental Leave – Canada</h1>
<p>In addition to Employment Insurance benefits, the company tops up your pay to
<strong>93% of your salary for 17 weeks</strong>. Inform HR at least 4 weeks before
the leave starts.</p>
""",
    },
    {
        "id": "2004",
        "title": "Hybrid Work – Canada",
        "space": "HR-CA",
        "language": "en",
        "labels": ["remote-work", "canada"],
        "read_groups": ["staff-ca", "hr-ca", "hr-global"],
        "version": 2,
        "updated": "2025-08-20T14:00:00Z",
        "body": """
<h1>Hybrid Work – Canada</h1>
<p>Employees work from the Toronto office <strong>3 days per week</strong> and remotely
the other days. Fully remote arrangements need VP approval.</p>
""",
    },
    # ------------------------------------------------------------ United States (English)
    {
        "id": "3001",
        "title": "Paid Time Off – United States",
        "space": "HR-US",
        "language": "en",
        "labels": ["leave", "united-states"],
        "read_groups": ["staff-us", "hr-us", "hr-global"],
        "version": 3,
        "updated": "2026-03-15T15:00:00Z",
        "body": """
<h1>Paid Time Off – United States</h1>
<h2>Entitlement</h2>
<p>Full-time employees receive <strong>20 days of PTO</strong> per year.</p>
<h2>How to request PTO</h2>
<p>Request PTO in Workday. Requests longer than 5 days need 3 weeks' notice.</p>
""",
    },
    {
        "id": "3002",
        "title": "Sick Leave – United States",
        "space": "HR-US",
        "language": "en",
        "labels": ["sick-leave", "united-states"],
        "read_groups": ["staff-us", "hr-us", "hr-global"],
        "version": 1,
        "updated": "2025-07-01T15:00:00Z",
        "body": """
<h1>Sick Leave – United States</h1>
<p>Employees accrue up to <strong>56 hours (7 days) of paid sick leave</strong> per year.</p>
""",
    },
    {
        "id": "3003",
        "title": "Parental Leave – United States",
        "space": "HR-US",
        "language": "en",
        "labels": ["parental-leave", "united-states"],
        "read_groups": ["staff-us", "hr-us", "hr-global"],
        "version": 2,
        "updated": "2026-02-01T15:00:00Z",
        "body": """
<h1>Parental Leave – United States</h1>
<p>Primary caregivers receive <strong>16 weeks of fully paid leave</strong>;
secondary caregivers receive <strong>6 weeks</strong>.</p>
""",
    },
    {
        "id": "3004",
        "title": "Hybrid Work – United States",
        "space": "HR-US",
        "language": "en",
        "labels": ["remote-work", "united-states"],
        "read_groups": ["staff-us", "hr-us", "hr-global"],
        "version": 1,
        "updated": "2025-06-15T15:00:00Z",
        "body": """
<h1>Hybrid Work – United States</h1>
<p>Employees work from the office <strong>2 days per week</strong>.</p>
""",
    },
    # ------------------------------------------------------------------ Global (English)
    {
        "id": "9001",
        "title": "Travel and Expenses Policy",
        "space": "HR-GLOBAL",
        "language": "en",
        "labels": ["expenses", "global"],
        "read_groups": ["all-staff"],
        "version": 7,
        "updated": "2026-05-05T10:00:00Z",
        "body": """
<h1>Travel and Expenses Policy</h1>
<h2>Meal allowance</h2>
<table>
<tr><th>Country</th><th>Daily cap</th></tr>
<tr><td>Italy</td><td>40 EUR</td></tr>
<tr><td>Canada</td><td>60 CAD</td></tr>
<tr><td>United States</td><td>60 USD</td></tr>
</table>
<h2>How to submit expenses</h2>
<ol>
<li>Upload receipts to SpendTool.</li>
<li>Submit the report within <strong>30 days</strong> of the expense.</li>
<li>Your manager approves; Finance reimburses within 10 business days.</li>
</ol>
""",
    },
    {
        "id": "9002",
        "title": "Code of Conduct",
        "space": "HR-GLOBAL",
        "language": "en",
        "labels": ["conduct", "global"],
        "read_groups": ["all-staff"],
        "version": 3,
        "updated": "2025-10-01T10:00:00Z",
        "body": """
<h1>Code of Conduct</h1>
<p>We treat colleagues, customers and partners with respect. Harassment and
discrimination are not tolerated. Report concerns to HR or through the anonymous
ethics line; retaliation against anyone who reports in good faith is prohibited.</p>
""",
    },
]

# --------------------------------------------------------------------------------------
# Drive-like benefits PDFs
# --------------------------------------------------------------------------------------

DRIVE_FILES: list[dict] = [
    {
        "id": "drv-it-welfare",
        "name": "Welfare aziendale 2026 - Italia.pdf",
        "modifiedTime": "2026-01-08T08:00:00Z",
        "permissions": ["staff-it@groups.example", "hr-it@groups.example"],
        "language": "it",
        "title": "Welfare aziendale 2026 - Italia",
        "sections": [
            (
                "Credito welfare",
                "Ogni dipendente riceve un credito welfare di 1.000 euro "
                "all'anno, utilizzabile sulla piattaforma welfare per istruzione, sport e salute.",
            ),
            (
                "Buoni pasto",
                "Buoni pasto elettronici da 8 euro per ogni giorno lavorato in "
                "ufficio o in smart working.",
            ),
            (
                "Assistenza sanitaria",
                "Fondo sanitario integrativo incluso per il dipendente; "
                "estendibile ai familiari a carico.",
            ),
        ],
    },
    {
        "id": "drv-ca-benefits",
        "name": "Benefits Guide 2026 - Canada.pdf",
        "modifiedTime": "2026-01-12T08:00:00Z",
        "permissions": ["staff-ca@groups.example", "hr-ca@groups.example"],
        "language": "en",
        "title": "Benefits Guide 2026 - Canada",
        "sections": [
            (
                "Health and dental",
                "Health and dental coverage through NorthShield starts on "
                "your first day, for you and your dependants.",
            ),
            ("Retirement", "The company matches RRSP contributions up to 4% of your salary."),
            ("Wellness", "A wellness allowance of 500 CAD per year for fitness and wellbeing."),
        ],
    },
    {
        "id": "drv-us-benefits",
        "name": "Benefits Overview 2026 - United States.pdf",
        "modifiedTime": "2026-01-12T08:00:00Z",
        "permissions": ["staff-us@groups.example", "hr-us@groups.example"],
        "language": "en",
        "title": "Benefits Overview 2026 - United States",
        "sections": [
            (
                "Medical plans",
                "Choice of two medical plans; the company pays 90% of the premium for employees.",
            ),
            ("Retirement", "401(k) with a company match of 4% of salary."),
            ("Commuter", "Commuter benefit of 100 USD per month."),
        ],
    },
]

# --------------------------------------------------------------------------------------
# HRIS
# --------------------------------------------------------------------------------------

EMPLOYEES: list[dict] = [
    # Demo users (used by the POC's tokens)
    {
        "id": "E001",
        "name": "Giulia Rossi",
        "email": "giulia.rossi@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "product",
        "role": "employee",
        "manager_id": "E010",
    },
    {
        "id": "E002",
        "name": "Liam Chen",
        "email": "liam.chen@flipp.example",
        "country": "CA",
        "office": "Toronto",
        "department": "engineering",
        "role": "employee",
        "manager_id": "E011",
    },
    {
        "id": "E003",
        "name": "Emma Johnson",
        "email": "emma.johnson@flipp.example",
        "country": "US",
        "office": "New York",
        "department": "sales",
        "role": "employee",
        "manager_id": "E012",
    },
    {
        "id": "E004",
        "name": "Marco Bianchi",
        "email": "marco.bianchi@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "people",
        "role": "hr",
        "hr_scope": "global",
        "manager_id": None,
    },
    # Other employees (background data)
    {
        "id": "E010",
        "name": "Francesca Conti",
        "email": "francesca.conti@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "product",
        "role": "employee",
        "manager_id": None,
    },
    {
        "id": "E011",
        "name": "Noah Tremblay",
        "email": "noah.tremblay@flipp.example",
        "country": "CA",
        "office": "Toronto",
        "department": "engineering",
        "role": "employee",
        "manager_id": None,
    },
    {
        "id": "E012",
        "name": "Olivia Smith",
        "email": "olivia.smith@flipp.example",
        "country": "US",
        "office": "New York",
        "department": "sales",
        "role": "employee",
        "manager_id": None,
    },
    {
        "id": "E013",
        "name": "Luca Ferrari",
        "email": "luca.ferrari@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "engineering",
        "role": "employee",
        "manager_id": "E010",
    },
    {
        "id": "E014",
        "name": "Sofia Ricci",
        "email": "sofia.ricci@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "sales",
        "role": "employee",
        "manager_id": "E010",
    },
    {
        "id": "E015",
        "name": "Ava Martin",
        "email": "ava.martin@flipp.example",
        "country": "CA",
        "office": "Toronto",
        "department": "product",
        "role": "employee",
        "manager_id": "E011",
    },
    {
        "id": "E016",
        "name": "Ethan Roy",
        "email": "ethan.roy@flipp.example",
        "country": "CA",
        "office": "Toronto",
        "department": "people",
        "role": "hr",
        "hr_scope": "country",
        "manager_id": None,
    },
    {
        "id": "E017",
        "name": "Mia Brown",
        "email": "mia.brown@flipp.example",
        "country": "US",
        "office": "New York",
        "department": "people",
        "role": "hr",
        "hr_scope": "country",
        "manager_id": None,
    },
    {
        "id": "E018",
        "name": "James Wilson",
        "email": "james.wilson@flipp.example",
        "country": "US",
        "office": "New York",
        "department": "engineering",
        "role": "employee",
        "manager_id": "E012",
    },
    {
        "id": "E019",
        "name": "Chiara Romano",
        "email": "chiara.romano@shopfully.example",
        "country": "IT",
        "office": "Milan",
        "department": "finance",
        "role": "employee",
        "manager_id": None,
    },
    {
        "id": "E020",
        "name": "Benjamin Lee",
        "email": "benjamin.lee@flipp.example",
        "country": "CA",
        "office": "Toronto",
        "department": "finance",
        "role": "employee",
        "manager_id": None,
    },
]

LEAVE_BALANCES: list[dict] = [
    {"employee_id": "E001", "year": 2026, "leave_type": "vacation", "entitled": 26, "taken": 14},
    {"employee_id": "E002", "year": 2026, "leave_type": "vacation", "entitled": 15, "taken": 11},
    {"employee_id": "E003", "year": 2026, "leave_type": "vacation", "entitled": 15, "taken": 9},
    {"employee_id": "E004", "year": 2026, "leave_type": "vacation", "entitled": 26, "taken": 20},
]

# Structured, non-personal, rarely changing: indexed as knowledge (see DESIGN §1.2).
# The US row deliberately conflicts with the US handbook (20 days) and is more recent.
LEAVE_POLICY_TABLE: list[dict] = [
    {
        "country": "IT",
        "leave_type": "vacation",
        "days_per_year": 26,
        "effective_from": "2026-01-01",
        "updated_at": "2026-01-05T08:00:00Z",
    },
    {
        "country": "CA",
        "leave_type": "vacation",
        "days_per_year": 15,
        "effective_from": "2026-01-01",
        "updated_at": "2026-01-05T08:00:00Z",
    },
    {
        "country": "US",
        "leave_type": "vacation",
        "days_per_year": 15,
        "effective_from": "2026-01-01",
        "updated_at": "2026-10-01T08:00:00Z",
    },
]


# --------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------


def write_confluence(out: Path) -> None:
    pages = out / "pages"
    pages.mkdir(parents=True)
    for page in PAGES:
        body = page["body"].strip() + f'\n<p class="disclaimer"><em>{DISCLAIMER}</em></p>\n'
        (pages / f"{page['id']}.html").write_text(body, encoding="utf-8")
        meta = {
            "id": page["id"],
            "type": "page",
            "title": page["title"],
            "space": {"key": page["space"]},
            "version": {"number": page["version"], "when": page["updated"]},
            "metadata": {
                "labels": page["labels"],
                "language": page["language"],
                # Custom page properties, as an HR team might maintain them.
                "properties": {
                    k: page[k]
                    for k in ("classification", "valid_from", "valid_to", "replaces")
                    if k in page
                },
            },
            "restrictions": {"read": {"groups": page["read_groups"]}},
            "_links": {"webui": f"https://wiki.example/pages/{page['id']}"},
        }
        (pages / f"{page['id']}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def write_pdf(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    for heading, text in sections:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, DISCLAIMER, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def write_drive(out: Path) -> None:
    files = out / "files"
    files.mkdir(parents=True)
    index = []
    for f in DRIVE_FILES:
        write_pdf(files / f["name"], f["title"], f["sections"])
        index.append(
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": "application/pdf",
                "modifiedTime": f["modifiedTime"],
                "permissions": [
                    {"type": "group", "emailAddress": e, "role": "reader"} for e in f["permissions"]
                ],
                "properties": {"language": f["language"]},
                "webViewLink": f"https://drive.example/file/{f['id']}",
            }
        )
    (out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))


def write_hris(out: Path) -> None:
    out.mkdir(parents=True)
    (out / "employees.json").write_text(json.dumps(EMPLOYEES, indent=2))
    (out / "leave_balances.json").write_text(json.dumps(LEAVE_BALANCES, indent=2))
    (out / "leave_policy_table.json").write_text(json.dumps(LEAVE_POLICY_TABLE, indent=2))


def main() -> None:
    for name in ("confluence-hr", "drive-benefits", "hris"):
        shutil.rmtree(ROOT / name, ignore_errors=True)
    write_confluence(ROOT / "confluence-hr")
    write_drive(ROOT / "drive-benefits")
    write_hris(ROOT / "hris")
    print(
        f"Generated {len(PAGES)} pages, {len(DRIVE_FILES)} PDFs, "
        f"{len(EMPLOYEES)} employees under {ROOT}"
    )


if __name__ == "__main__":
    main()
