import os
import gspread
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Define Google Sheets API scope
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# Authenticate using OAuth 2.0
def authenticate_google_sheets():
    creds = None

    # Use OAuth flow for user authentication
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    # Save credentials for future use
    with open("token.json", "w") as token:
        token.write(creds.to_json())

    print("✅ Authentication successful! Token saved as 'token.json'.")

# Run authentication
authenticate_google_sheets()
