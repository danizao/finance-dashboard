"""
Daily Finance Data Fetcher — Enable Banking API → Dashboard
============================================================
This script connects to your Portuguese bank accounts via Enable Banking's
Open Banking (PSD2) API, pulls yesterday's + month-to-date transactions,
and updates the HTML dashboard with real data.

Setup:
1. Sign up at https://enablebanking.com/sign-in/
2. Register an application in the Control Panel
3. Download the .pem private key file
4. Run this script once interactively to link your bank accounts
5. After that, the scheduled task runs it daily at 8 AM

Usage:
    python fetch_transactions.py --setup          # First-time bank linking
    python fetch_transactions.py                  # Daily data fetch + dashboard update
    python fetch_transactions.py --check          # Verify connections are alive
"""

import json
import os
import sys
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import jwt as pyjwt
    import requests
except ImportError:
    print("Installing required packages...")
    os.system("pip install PyJWT requests cryptography --break-system-packages -q")
    import jwt as pyjwt
    import requests

# ============================================================
# CONFIGURATION — Edit these values after signing up
# ============================================================

CONFIG_DIR = Path(__file__).parent / ".config"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"
DASHBOARD_FILE = Path(__file__).parent / "Finance-Dashboard_Dashboard_v1.html"

# Your Enable Banking application ID (from Control Panel)
# This is also the name of your .pem key file
# ⚠️  MUST be a PRODUCTION app — Sandbox only shows mock banks
# Replace this with your new Production APP_ID after following setup instructions
APP_ID = "dadfd229-f113-44ba-bacb-b3f58f7aa11c"  # ← REPLACE with Production ID

# Path to your .pem private key (downloaded when you registered the app)
PEM_FILE = str(CONFIG_DIR / f"{APP_ID}.pem")

# Redirect URL (set this in your Enable Banking app registration)
REDIRECT_URL = "https://enablebanking.com/redirect"

# Bank configuration — maps your banks to Enable Banking ASPSP names
BANKS = {
    "CGD": {
        "aspsp_name": "Caixa Geral de Depositos",
        "country": "PT",
        "display_name": "CGD"
    },
    "Bankinter": {
        "aspsp_name": "Bankinter",
        "country": "PT",
        "display_name": "Bankinter"
    },
    "ActivoBank": {
        "aspsp_name": "ActivoBank",
        "country": "PT",
        "display_name": "ActivoBank"
    },
    "Revolut": {
        "aspsp_name": "Revolut",
        "country": "LT",  # Revolut is licensed in Lithuania
        "display_name": "Revolut"
    }
}


# ============================================================
# API HELPERS
# ============================================================

def get_auth_headers():
    """Generate JWT-based authorization headers."""
    private_key = open(PEM_FILE, "rb").read()
    iat = int(datetime.now().timestamp())
    jwt_body = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": iat,
        "exp": iat + 3600,
    }
    token = pyjwt.encode(
        jwt_body, private_key, algorithm="RS256",
        headers={"kid": APP_ID}
    )
    return {"Authorization": f"Bearer {token}"}


def api_get(endpoint, params=None):
    """Make authenticated GET request to Enable Banking API."""
    r = requests.get(
        f"https://api.enablebanking.com{endpoint}",
        headers=get_auth_headers(),
        params=params
    )
    r.raise_for_status()
    return r.json()


def api_post(endpoint, body):
    """Make authenticated POST request to Enable Banking API."""
    r = requests.post(
        f"https://api.enablebanking.com{endpoint}",
        headers=get_auth_headers(),
        json=body
    )
    if not r.ok:
        print(f"\n  API Error {r.status_code}: {r.text}")
    r.raise_for_status()
    return r.json()


# ============================================================
# BANK LINKING (first-time setup)
# ============================================================

def list_available_banks():
    """Show all available banks for our target countries."""
    for country, label in [("PT", "Portugal"), ("LT", "Lithuania (Revolut)")]:
        print(f"\n--- {label} ---")
        data = api_get("/aspsps", params={"country": country})
        banks = data.get("aspsps", [])
        if not banks:
            print("  (no banks returned — check if your app is in PRODUCTION mode)")
        for bank in banks:
            print(f"  • {bank['name']}")


def get_all_aspsp_names():
    """Fetch all available bank names from the API for PT and LT."""
    all_banks = {}
    for country in ["PT", "LT"]:
        data = api_get("/aspsps", params={"country": country})
        for bank in data.get("aspsps", []):
            all_banks[bank["name"]] = country
    return all_banks


def pick_aspsp(display_name, all_banks):
    """Show bank list and let user pick the correct API name."""
    print(f"\n  Which bank in the list corresponds to '{display_name}'?")
    bank_list = sorted(all_banks.keys())
    for i, name in enumerate(bank_list, 1):
        print(f"    {i:>3}. {name}  [{all_banks[name]}]")
    print(f"      0. Skip {display_name}")
    while True:
        choice = input(f"\n  Enter number (0 to skip): ").strip()
        try:
            idx = int(choice)
            if idx == 0:
                return None, None
            if 1 <= idx <= len(bank_list):
                chosen = bank_list[idx - 1]
                return chosen, all_banks[chosen]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


def link_bank_by_aspsp(bank_key, display_name, aspsp_name, country):
    """Start the OAuth flow to link a bank account with a known ASPSP name."""
    import uuid
    state = str(uuid.uuid4())

    valid_until = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "access": {"valid_until": valid_until},
        "aspsp": {"name": aspsp_name, "country": country},
        "state": state,
        "redirect_url": REDIRECT_URL,
        "psu_type": "personal",
    }

    print(f"\n--- Linking {display_name} ({aspsp_name}) ---")
    result = api_post("/auth", body)
    auth_url = result.get("url")
    if not auth_url:
        print("  ERROR: No auth URL returned.")
        return None

    print(f"\n  Open this URL in your browser:")
    print(f"  {auth_url}")
    print(f"\n  After authorizing, you'll land on a 'Page not found' page — that's normal.")
    print(f"  Copy the FULL URL from the address bar and paste it below.")

    redirect_response = input("\n  Paste redirect URL: ").strip()

    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_response)
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        print("  ERROR: Could not find 'code' in the URL. Make sure to paste the full URL.")
        return None

    session = api_post("/sessions", {"code": code})
    n_accounts = len(session.get("accounts", []))
    print(f"\n  ✓ {display_name} linked! ({n_accounts} account(s) found)")

    return {
        "bank_key": bank_key,
        "display_name": display_name,
        "aspsp_name": aspsp_name,
        "country": country,
        "session_id": session.get("session_id"),
        "accounts": session.get("accounts", []),
        "linked_at": datetime.now().isoformat(),
        "valid_until": valid_until,
    }


def setup():
    """Interactive setup to link bank accounts, picking from the live API list."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    print("\nFetching available banks from Enable Banking API...")
    all_banks = get_all_aspsp_names()
    print(f"  Found {len(all_banks)} banks across PT and LT.")

    # Load existing sessions if any
    sessions = {}
    if SESSIONS_FILE.exists():
        with open(SESSIONS_FILE) as f:
            sessions = json.load(f)
        print(f"  {len(sessions)} bank(s) already linked: {', '.join(sessions.keys())}")

    your_banks = ["CGD", "Bankinter", "ActivoBank", "Revolut"]

    for bank_label in your_banks:
        if bank_label in sessions:
            relink = input(f"\n  {bank_label} is already linked. Re-link? (y/n): ").strip().lower()
            if relink != "y":
                continue

        do_link = input(f"\n  Link {bank_label}? (y/n): ").strip().lower()
        if do_link != "y":
            continue

        aspsp_name, country = pick_aspsp(bank_label, all_banks)
        if not aspsp_name:
            print(f"  Skipping {bank_label}.")
            continue

        session_data = link_bank_by_aspsp(bank_label, bank_label, aspsp_name, country)
        if session_data:
            sessions[bank_label] = session_data

    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  Setup complete! {len(sessions)} bank(s) linked.")
    print(f"  Run without --setup to fetch transactions and update the dashboard.")
    print(f"{'=' * 60}")


# ============================================================
# TRANSACTION FETCHING
# ============================================================

def load_sessions():
    """Load saved bank sessions."""
    if not SESSIONS_FILE.exists():
        print("No sessions found. Run with --setup first.")
        sys.exit(1)
    with open(SESSIONS_FILE) as f:
        return json.load(f)


def fetch_transactions_for_account(account_uid, date_from, date_to):
    """Fetch transactions for a specific account within a date range."""
    all_transactions = []
    params = {"date_from": date_from, "date_to": date_to}

    try:
        data = api_get(f"/accounts/{account_uid}/transactions", params=params)
        txns = data.get("transactions", [])
        all_transactions.extend(txns)

        # Handle pagination
        continuation_key = data.get("continuation_key")
        while continuation_key:
            params["continuation_key"] = continuation_key
            data = api_get(f"/accounts/{account_uid}/transactions", params=params)
            txns = data.get("transactions", [])
            all_transactions.extend(txns)
            continuation_key = data.get("continuation_key")

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print(f"  ⚠ Account {account_uid[:12]}... needs re-authorization (consent expired)")
        else:
            print(f"  ⚠ Error fetching {account_uid[:12]}...: {e}")
    except Exception as e:
        print(f"  ⚠ Unexpected error: {e}")

    return all_transactions


def fetch_balance(account_uid):
    """Fetch current balance for an account."""
    try:
        data = api_get(f"/accounts/{account_uid}/balances")
        balances = data.get("balances", [])
        if balances:
            # Prefer 'expected' or 'closingBooked' balance type
            for b in balances:
                if b.get("balance_type") in ("expected", "closingBooked", "interimAvailable"):
                    return float(b.get("balance_amount", {}).get("amount", 0))
            # Fallback: return first balance
            return float(balances[0].get("balance_amount", {}).get("amount", 0))
    except Exception as e:
        print(f"  ⚠ Error fetching balance for {account_uid[:12]}...: {e}")
    return 0.0


def tag_person(account_name):
    """Tag a transaction or balance with the account holder's first name."""
    name = account_name.upper()
    if "DANIEL" in name or "BETTENCOURT" in name:
        return "Daniel"
    if "SANDRA" in name or "GUSMAO" in name or "GUSMÃO" in name:
        return "Sandra"
    return "Shared"


def fetch_all_data():
    """Fetch transactions and balances from all linked banks."""
    sessions = load_sessions()
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    first_of_month = today.replace(day=1)

    date_from = first_of_month.isoformat()
    date_to = yesterday.isoformat()

    all_transactions = []
    all_balances = []

    for bank_key, session_data in sessions.items():
        bank_display = BANKS.get(bank_key, {}).get("display_name", bank_key)
        print(f"\nFetching {bank_display}...")

        for account in session_data.get("accounts", []):
            uid = account["uid"]
            account_name = account.get("name", account.get("uid", "Unknown"))

            # Fetch transactions
            txns = fetch_transactions_for_account(uid, date_from, date_to)
            print(f"  {account_name}: {len(txns)} transactions")

            for t in txns:
                amount_obj = t.get("transaction_amount", {})
                amount = float(amount_obj.get("amount", 0))

                # Apply sign based on credit_debit_indicator.
                # DBIT = money leaving the account = negative (expense)
                # CRDT = money entering the account = positive (income)
                # If the field is absent, trust the sign already on the amount.
                indicator = t.get("credit_debit_indicator", "").upper()
                if indicator == "DBIT":
                    amount = -abs(amount)
                elif indicator == "CRDT":
                    amount = abs(amount)
                # No indicator → keep as-is (already signed by the bank)

                # Categorize AFTER sign correction so the fallback uses the real sign
                all_transactions.append({
                    "date": t.get("booking_date", t.get("value_date", date_to)),
                    "description": (
                        t.get("remittance_information_unstructured", "") or
                        t.get("creditor_name", "") or
                        t.get("debtor_name", "") or
                        "Unknown"
                    ).strip()[:80],
                    "category": categorize_transaction(t, amount),
                    "amount": amount,
                    "account": account_name,
                    "bank": bank_display,
                    "person": tag_person(account_name),
                })

            # Fetch balance
            balance = fetch_balance(uid)
            all_balances.append({
                "account": account_name,
                "bank": bank_display,
                "balance": balance,
                "person": tag_person(account_name),
            })
            print(f"  {account_name} balance: €{balance:,.2f}")

    # Reconcile own-account transfers (must run after all transactions are collected)
    all_transactions = reconcile_transfers(all_transactions)

    return {
        "generated": datetime.now().isoformat(),
        "month": today.strftime("%B %Y"),
        "yesterday": yesterday.isoformat(),
        "transactions": all_transactions,
        "balances": all_balances
    }


# ============================================================
# TRANSACTION CATEGORIZATION
# ============================================================

CATEGORY_RULES = {
    "Income": [
        "salary", "salário", "ordenado", "vencimento", "transfer from",
        "invoice", "fatura", "consulting", "rendimento", "reembolso"
    ],
    "Groceries": [
        "continente", "pingo doce", "lidl", "aldi", "minipreço",
        "intermarché", "mercadona", "supermercado", "mercearia"
    ],
    "Dining": [
        "restaurante", "restaurant", "café", "cafetaria", "mcdonald",
        "burger", "pizza", "uber eats", "glovo", "bolt food"
    ],
    "Transport": [
        "galp", "bp", "repsol", "cepsa", "combustível", "gasolina",
        "uber", "bolt", "taxi", "cp ", "metro", "carris", "via verde",
        "portagem", "estacionamento", "parking"
    ],
    "Utilities": [
        "edp", "epal", "água", "gás", "electricidade", "energia",
        "vodafone", "meo", "nos ", "nowo", "internet", "telecomunicações"
    ],
    "Housing": [
        "renda", "rent", "hipoteca", "mortgage", "condomínio",
        "seguros habitação", "imobiliária"
    ],
    "Health": [
        "farmácia", "pharmacy", "médico", "hospital", "clínica",
        "saúde", "health", "dentist", "dentista", "ótica"
    ],
    "Subscriptions": [
        "netflix", "spotify", "youtube", "disney", "hbo", "apple",
        "google storage", "amazon prime", "adobe", "microsoft 365",
        "chatgpt", "claude", "github"
    ],
    "Shopping": [
        "worten", "fnac", "ikea", "zara", "h&m", "primark",
        "amazon", "aliexpress", "el corte inglés"
    ],
    "Insurance": [
        "seguro", "insurance", "allianz", "fidelidade", "ageas",
        "tranquilidade", "liberty"
    ],
    "Transfers": [
        "transferência", "transfer", "mbway", "mb way"
    ],
}


def categorize_transaction(txn, corrected_amount=None):
    """Auto-categorize based on description keywords.
    corrected_amount: the sign-corrected amount (positive=income, negative=expense).
    """
    desc = (
        (txn.get("remittance_information_unstructured", "") or "") +
        " " +
        (txn.get("creditor_name", "") or "") +
        " " +
        (txn.get("debtor_name", "") or "")
    ).lower()

    for category, keywords in CATEGORY_RULES.items():
        if category == "Income":
            continue  # Don't match Income via keywords — use amount sign below
        for keyword in keywords:
            if keyword in desc:
                return category

    # Use the corrected (sign-adjusted) amount to decide income vs other
    amount = corrected_amount if corrected_amount is not None else float(txn.get("transaction_amount", {}).get("amount", 0))
    if amount > 0:
        return "Income"

    return "Other"


# ============================================================
# TRANSFER RECONCILIATION
# ============================================================

def reconcile_transfers(transactions):
    """Detect own-account transfers by matching outgoing and incoming transactions
    of the same amount across different accounts within a 4-day window.

    A transfer is when money leaves one account (negative) and the exact same
    amount arrives in a different account (positive) within ~4 days.
    Both legs are marked with is_transfer=True and category='Transfer'.
    """
    WINDOW_DAYS = 4

    outgoing = [(i, t) for i, t in enumerate(transactions) if t["amount"] < 0]
    incoming = [(i, t) for i, t in enumerate(transactions) if t["amount"] > 0]

    matched = set()

    # Sort outgoing by absolute amount descending (match large transfers first)
    outgoing_sorted = sorted(outgoing, key=lambda x: abs(x[1]["amount"]), reverse=True)

    for out_idx, out_t in outgoing_sorted:
        if out_idx in matched:
            continue

        out_amount = abs(out_t["amount"])
        out_date = datetime.fromisoformat(out_t["date"])

        best_in_idx = None
        best_diff = float("inf")

        for in_idx, in_t in incoming:
            if in_idx in matched:
                continue

            # Must be a different account (different bank OR different account name)
            if in_t["account"] == out_t["account"] and in_t["bank"] == out_t["bank"]:
                continue

            # Amount must match within 1 cent
            if abs(abs(in_t["amount"]) - out_amount) > 0.01:
                continue

            # Must fall within the time window
            in_date = datetime.fromisoformat(in_t["date"])
            diff = abs((in_date - out_date).days)
            if diff > WINDOW_DAYS:
                continue

            # Prefer the closest match in time
            if diff < best_diff:
                best_diff = diff
                best_in_idx = in_idx

        if best_in_idx is not None:
            matched.add(out_idx)
            matched.add(best_in_idx)
            transactions[out_idx]["category"] = "Transfer"
            transactions[out_idx]["is_transfer"] = True
            transactions[best_in_idx]["category"] = "Transfer"
            transactions[best_in_idx]["is_transfer"] = True

    # Tag all unmatched transactions as non-transfers
    for i, t in enumerate(transactions):
        if "is_transfer" not in t:
            t["is_transfer"] = False

    n_pairs = len(matched) // 2
    if n_pairs:
        print(f"\n  Reconciled {n_pairs} transfer pair(s) — excluded from income/expense totals.")

    return transactions


# ============================================================
# DASHBOARD UPDATE
# ============================================================

def update_dashboard(data):
    """Replace the DATA section in the HTML dashboard with real data."""
    if not DASHBOARD_FILE.exists():
        print(f"Dashboard file not found: {DASHBOARD_FILE}")
        return False

    html = DASHBOARD_FILE.read_text(encoding="utf-8")

    # Build the new DATA block
    data_json = json.dumps(data, indent=12, ensure_ascii=False)
    new_data_block = f"const DATA = {data_json};"

    # Replace the existing DATA block using regex
    pattern = r"const DATA = \{[\s\S]*?\};\s*\n"
    replacement = new_data_block + "\n"

    # More robust: find the DATA assignment and replace up to the matching closing brace
    start_marker = "const DATA = {"
    start_idx = html.find(start_marker)
    if start_idx == -1:
        print("ERROR: Could not find 'const DATA = {' in dashboard HTML")
        return False

    # Find the matching closing brace by counting braces
    brace_count = 0
    end_idx = start_idx + len(start_marker) - 1  # Position of first {
    for i in range(end_idx, len(html)):
        if html[i] == "{":
            brace_count += 1
        elif html[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                end_idx = i
                break

    # Find the semicolon after the closing brace
    semi_idx = html.find(";", end_idx)
    if semi_idx != -1:
        end_idx = semi_idx + 1

    new_html = html[:start_idx] + new_data_block + html[end_idx + 1:]
    DASHBOARD_FILE.write_text(new_html, encoding="utf-8")
    print(f"\nDashboard updated: {DASHBOARD_FILE}")
    return True


# ============================================================
# SUMMARY
# ============================================================

def print_summary(data):
    """Print a quick text summary of the day."""
    yesterday = data["yesterday"]
    txns = data["transactions"]

    y_txns = [t for t in txns if t["date"] == yesterday]
    y_income = sum(t["amount"] for t in y_txns if t["amount"] > 0 and not t.get("is_transfer"))
    y_expense = sum(t["amount"] for t in y_txns if t["amount"] < 0 and not t.get("is_transfer"))

    mtd_expense_by_cat = {}
    for t in txns:
        if t["amount"] < 0 and not t.get("is_transfer"):
            mtd_expense_by_cat[t["category"]] = mtd_expense_by_cat.get(t["category"], 0) + abs(t["amount"])

    top_cats = sorted(mtd_expense_by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
    total_balance = sum(b["balance"] for b in data["balances"])

    print(f"\n{'=' * 50}")
    print(f"  DAILY SUMMARY — {yesterday}")
    print(f"{'=' * 50}")
    print(f"  Yesterday: {len(y_txns)} transactions")
    print(f"    Income:   €{y_income:>10,.2f}")
    print(f"    Expenses: €{y_expense:>10,.2f}")
    print(f"    Net:      €{(y_income + y_expense):>10,.2f}")
    print(f"\n  Top MTD expense categories:")
    for cat, amount in top_cats:
        print(f"    {cat:<20s} €{amount:>10,.2f}")
    print(f"\n  Total balance (all banks): €{total_balance:>10,.2f}")
    for b in data["balances"]:
        print(f"    {b['bank']:<20s} €{b['balance']:>10,.2f}")
    print(f"{'=' * 50}\n")


# ============================================================
# CHECK CONNECTIONS
# ============================================================

def check_connections():
    """Verify all bank connections are still valid."""
    sessions = load_sessions()
    print("\n--- Connection Status ---")
    for bank_key, session_data in sessions.items():
        bank_display = BANKS.get(bank_key, {}).get("display_name", bank_key)
        valid_until = session_data.get("valid_until", "unknown")
        n_accounts = len(session_data.get("accounts", []))
        expired = False
        try:
            exp_date = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            if exp_date < datetime.now(timezone.utc):
                expired = True
        except Exception:
            pass

        status = "EXPIRED — run --setup to re-link" if expired else "OK"
        print(f"  {bank_display}: {n_accounts} accounts, valid until {valid_until[:10]} [{status}]")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    elif "--check" in sys.argv:
        check_connections()
    elif "--list-banks" in sys.argv:
        list_available_banks()
    else:
        print("Fetching daily finance data...")
        data = fetch_all_data()
        update_dashboard(data)
        print_summary(data)
