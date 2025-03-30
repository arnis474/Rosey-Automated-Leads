import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Define the required scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def authenticate_google_sheets():
    """Authenticate manually by copying the auth URL into the correct browser."""
    if not os.path.exists("client_secret.json"):
        print("❌ 'client_secret.json' not found. Please download it from Google Cloud Console.")
        return

    # Create OAuth flow
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

    # Generate the auth URL manually
    auth_url, _ = flow.authorization_url(prompt='consent')

    print("🔗 Copy and paste this link into your WORK Chrome profile:")
    print(auth_url)
    print()

    # Prompt user to paste the returned code
    code = input("📥 After authorizing, paste the code from the URL here: ")

    # Exchange code for credentials
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Save token for future use
    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print("✅ Authentication successful! Token saved as 'token.json'.")

if __name__ == "__main__":
    authenticate_google_sheets()
