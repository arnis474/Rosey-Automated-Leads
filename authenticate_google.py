import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def authenticate_google_sheets():
    if not os.path.exists("client_secret.json"):
        print("❌ Missing 'client_secret.json'. Please download it from Google Cloud Console.")
        return

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)

    # 👇 prevent opening browser automatically
    creds = flow.run_local_server(
        port=8080,
        prompt='consent',
        open_browser=False  # ✅ prevent auto-open of personal Chrome
    )

    with open("token.json", "w") as token_file:
        token_file.write(creds.to_json())

    print("✅ Authentication complete! token.json saved.")

if __name__ == "__main__":
    authenticate_google_sheets()
