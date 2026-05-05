import os
import time
import hmac
import hashlib
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from slack_sdk import WebClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas



load_dotenv()

app = FastAPI()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

slack_client = WebClient(token=SLACK_BOT_TOKEN)

AUTHORIZED_USERS = {
    # Add Slack user IDs here
    # Example: "U012ABCDEF"
}

DUMMY_DTA_DATA = {
    "DL12345": [
        ["2026-02-01", "Purchase", "CAR-1001", 250000, 0, 250000],
        ["2026-02-02", "Credit", "Adjustment", 0, 50000, 200000],
        ["2026-02-03", "Service Fee", "Platform Charge", 2000, 0, 202000],
        ["2026-02-04", "Debit", "Penalty", 15000, 0, 217000],
    ],
    "DL67890": [
        ["2026-02-01", "Purchase", "CAR-2001", 180000, 0, 180000],
        ["2026-02-05", "Forfeiture", "Token Forfeit", 10000, 0, 190000],
        ["2026-02-06", "Credit", "Refund", 0, 25000, 165000],
    ],
}


def verify_slack_request(request_body: bytes, timestamp: str, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False

    base_string = f"v0:{timestamp}:{request_body.decode()}".encode()
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        base_string,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(my_signature, signature)


def generate_pdf(dealer_code: str, rows: list) -> str:
    output_dir = Path("reports")
    output_dir.mkdir(exist_ok=True)

    file_path = output_dir / f"DTA_Statement_{dealer_code}.pdf"

    c = canvas.Canvas(str(file_path), pagesize=A4)
    width, height = A4

    y = height - 50

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Dealer Transaction Account Statement")

    y -= 30
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Dealer Code: {dealer_code}")
    y -= 18
    c.drawString(50, y, f"Generated On: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    y -= 40
    c.setFont("Helvetica-Bold", 9)
    headers = ["Date", "Type", "Reference", "Debit", "Credit", "Balance"]
    x_positions = [50, 120, 210, 330, 400, 470]

    for x, header in zip(x_positions, headers):
        c.drawString(x, y, header)

    y -= 15
    c.setFont("Helvetica", 8)

    final_balance = 0

    for row in rows:
        date, txn_type, ref, debit, credit, balance = row
        final_balance = balance

        values = [
            date,
            txn_type,
            ref,
            f"Rs. {debit}",
            f"Rs. {credit}",
            f"Rs. {balance}",
        ]

        for x, value in zip(x_positions, values):
            c.drawString(x, y, str(value))

        y -= 18

    y -= 25
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, f"Final Running Balance: Rs. {final_balance}")

    c.save()

    return str(file_path)


def process_dta_request(dealer_code: str, channel_id: str, thread_ts: str):
    rows = DUMMY_DTA_DATA.get(dealer_code.upper())

    if not rows:
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"No dummy DTA data found for dealer code `{dealer_code}`."
        )
        return

    pdf_path = generate_pdf(dealer_code.upper(), rows)

    slack_client.files_upload_v2(
        channel=channel_id,
        thread_ts=thread_ts,
        file=pdf_path,
        title=f"DTA Statement - {dealer_code.upper()}",
        initial_comment=f"Dummy DTA statement generated for `{dealer_code.upper()}`."
    )


@app.post("/slack/get-dta")
async def get_dta(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    signature = request.headers.get("X-Slack-Signature")

    if not verify_slack_request(body, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack request")

    form = await request.form()

    user_id = form.get("user_id")
    channel_id = form.get("channel_id")
    text = form.get("text", "").strip()
    response_url = form.get("response_url")

    if AUTHORIZED_USERS and user_id not in AUTHORIZED_USERS:
        return {
            "response_type": "ephemeral",
            "text": "You are not authorized to use this DTA command."
        }

    if not text:
        return {
            "response_type": "ephemeral",
            "text": "Please enter dealer code. Example: `/get-dta DL12345`"
        }

    dealer_code = text.split()[0]

    background_tasks.add_task(
        process_dta_request,
        dealer_code,
        channel_id,
        None
    )

    return {
        "response_type": "ephemeral",
        "text": f"Generating dummy DTA statement for `{dealer_code}`..."
    }