import gspread
import os
import streamlit as st
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from dotenv import load_dotenv

# Load API keys from .env file
load_dotenv()
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Leads")

# Function to connect to Google Sheets
def connect_to_google_sheets():
    try:
        if not os.path.exists("token.json"):
            print("❌ Authentication file (token.json) not found. Please follow setup instructions.")
            return None
        
        creds = Credentials.from_authorized_user_file(
            "token.json", 
            ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        client = gspread.authorize(creds)

        # Try to access the specific worksheet "Leads"
        sheet = client.open(SPREADSHEET_NAME).worksheet("Leads")
        print(f"✅ Successfully connected to Google Sheet: {SPREADSHEET_NAME} (Tab: Leads)")
        return sheet

    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ Spreadsheet '{SPREADSHEET_NAME}' not found. Check your .env file.")
        return None
    except gspread.exceptions.WorksheetNotFound:
        print(f"❌ Worksheet 'Leads' not found. Ensure a tab named 'Leads' exists in your spreadsheet.")
        return None
    except Exception as e:
        print(f"❌ Error connecting to Google Sheets: {e}")
        return None

# Function to test Google Sheets connection
def test_google_sheets_connection():
    sheet = connect_to_google_sheets()
    if sheet:
        test_lead = ["Test Business", "123 Test Street", "Test City", "Test Type", "5.0", "123-456-7890", "www.test.com"]
        try:
            sheet.append_row(test_lead)
            print("✅ Test lead successfully added to Google Sheets!")
        except gspread.exceptions.APIError as e:
            print(f"❌ Google Sheets API Error: {e}")
    else:
        print("❌ Google Sheets connection failed.")

# Run the test
if __name__ == "__main__":
    test_google_sheets_connection()
