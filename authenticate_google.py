import os
import gspread
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Define the required scopes for Sheets and Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def authenticate_google_sheets():
    # Start OAuth flow using installed application method
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

    # Use run_console() to manually authorize and avoid redirect_uri issues
    creds = flow.run_console()

    # Save the token for future use
    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print("✅ Authentication successful! Token saved as 'token.json'.")

# Execute the authentication function
if __name__ == "__main__":
    authenticate_google_sheets()

