import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Define the required Google Sheets and Drive API scopes
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def authenticate_google_sheets():
    """Runs the OAuth 2.0 flow, saves token.json for future use."""
    
    # Ensure credentials file exists
    if not os.path.exists("client_secret.json"):
        print("❌ 'client_secret.json' not found. Please download it from Google Cloud Console.")
        return

    # Start the OAuth 2.0 flow
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

    # Launch browser-based login and handle redirect safely
    creds = flow.run_local_server(
        port=8080,
        prompt='consent',
        authorization_prompt_message="🔗 Please open this link in your work Chrome profile to log in:"
    )

    # Save the authorized token to token.json
    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print("✅ Authentication successful! Token saved as 'token.json'.")

# Run authentication if this script is executed
if __name__ == "__main__":
    authenticate_google_sheets()
