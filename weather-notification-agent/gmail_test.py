import os.path
import base64

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_email_body(payload):
    """
    Extract plain-text email content from Gmail message payload.
    """

    # Simple non-multipart email
    if "body" in payload:
        data = payload["body"].get("data")

        if data:
            decoded = base64.urlsafe_b64decode(data).decode(
                "utf-8",
                errors="ignore"
            )
            return decoded

    # Multipart email
    parts = payload.get("parts", [])

    for part in parts:

        mime_type = part.get("mimeType", "")

        # Prefer plain text
        if mime_type == "text/plain":
            data = part.get("body", {}).get("data")

            if data:
                return base64.urlsafe_b64decode(data).decode(
                    "utf-8",
                    errors="ignore"
                )

        # Check nested parts
        if part.get("parts"):
            body = get_email_body(part)

            if body:
                return body

    return "No readable email body found."


def main():

    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file(
            "token.json",
            SCOPES
        )

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES
            )

            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build(
        "gmail",
        "v1",
        credentials=creds
    )

    query = (
        'newer_than:30d '
        '{subject:plant '
        'subject:watering '
        'subject:garden '
        'subject:gardening '
        'subject:weather '
        '"watering reminder" '
        '"plant reminder"}'
    )

    results = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=10
    ).execute()

    messages = results.get("messages", [])

    if not messages:
        print("No relevant GreenPulse emails found.")
        return

    print("Gmail connection successful")
    print("Found", len(messages), "relevant messages")

    for message in messages:

        msg = service.users().messages().get(
            userId="me",
            id=message["id"],
            format="full"
        ).execute()

        payload = msg["payload"]
        headers = payload["headers"]

        subject = "No Subject"
        sender = "Unknown Sender"
        date = "Unknown Date"

        for header in headers:

            if header["name"] == "Subject":
                subject = header["value"]

            elif header["name"] == "From":
                sender = header["value"]

            elif header["name"] == "Date":
                date = header["value"]

        body = get_email_body(payload)

        print("\n================================")
        print("From:", sender)
        print("Subject:", subject)
        print("Date:", date)

        print("\nEmail Body:")
        print(body)

        print("================================")


if __name__ == "__main__":
    main()