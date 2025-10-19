import os
import traceback
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import mysql.connector
from plaid_client import client
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products

load_dotenv()
app = Flask(__name__)

# -----------------------
# MySQL configuration
# -----------------------
db_config = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "personal_finance")
}

# -----------------------
# Database helper functions
# -----------------------
def save_access_token(institution_id, token):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO access_tokens (institution_id, access_token)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE access_token=%s
        """, (institution_id, token, token))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def save_transactions(transactions, institution_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        for tx in transactions:
            cursor.execute("""
                INSERT IGNORE INTO transactions
                (transaction_id, date, name, amount, account_id, category, institution_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                tx["transaction_id"],
                tx["date"],
                tx.get("name"),
                tx.get("amount"),
                tx.get("account_id"),
                ", ".join(tx.get("category", [])) if tx.get("category") else None,
                institution_id
            ))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

# -----------------------
# Routes
# -----------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/create_link_token", methods=["POST"])
def create_link_token():
    try:
        req = LinkTokenCreateRequest(
            products=[Products("transactions")],
            client_name="Personal Finance Tracker",
            country_codes=[CountryCode("US")],
            language="en",
            user=LinkTokenCreateRequestUser(client_user_id="user-123"),
            redirect_uri=os.getenv("PLAID_REDIRECT_URI")
        )
        response = client.link_token_create(req)
        return jsonify({"link_token": response.to_dict()["link_token"]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/exchange_public_token", methods=["POST"])
def exchange_public_token():
    try:
        public_token = request.json.get("public_token")
        metadata = request.json.get("metadata")  # receive metadata from front end

        # fallback institution_id
        if metadata and "institution" in metadata and "institution_id" in metadata["institution"]:
            institution_id = metadata["institution"]["institution_id"]
        else:
            institution_id = "unknown_institution"

        resp = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token)
        )
        access_token = resp.to_dict()["access_token"]

        # Save access token in DB
        save_access_token(institution_id, access_token)
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/sync_transactions", methods=["GET"])
def sync_transactions():
    try:
        # Fetch all access tokens from DB
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT access_token, institution_id FROM access_tokens")
        tokens = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    if not tokens:
        return jsonify({"error": "No access token. Please link your bank first."}), 400

    all_transactions = []

    for access_token, institution_id in tokens:
        try:
            start_date = (datetime.now() - timedelta(days=365*2)).date()
            end_date = datetime.now().date()
            req = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date
            )
            resp = client.transactions_get(req)
            transactions = resp.to_dict().get("transactions", [])
            save_transactions(transactions, institution_id)
            all_transactions.extend(transactions)
        except Exception as e:
            msg = str(e)
            if "PRODUCT_NOT_READY" in msg:
                continue
            traceback.print_exc()

    all_transactions = sorted(all_transactions, key=lambda x: x["date"], reverse=True)
    return jsonify({"transactions": all_transactions})

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    app.run(debug=True)
