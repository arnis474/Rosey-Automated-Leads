import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def authenticate_google_sheets():
    if not os.path.exists("client_secret.json"):
        print("❌ Missing client_secret.json in current directory.")
        return

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

    # This opens the browser — you can ignore it and use the URL from terminal
    creds = flow.run_local_server(
        port=8080,
        prompt='consent',
        authorization_prompt_message="🔗 Copy this URL into your work Chrome profile to continue login:"
    )

    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print("✅ Token saved as 'token.json'.")

if __name__ == "__main__":
    authenticate_google_sheets()
