import streamlit as st
import requests
import json
import os
import re
import gspread
import time
import logging
import urllib.parse
import random
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Try to import stqdm for non-freezing progress tracking
try:
    from stqdm import stqdm
    HAVE_STQDM = True
except ImportError:
    HAVE_STQDM = False
    logger.info("stqdm not installed. Using standard progress tracking.")

# Load API keys from .env file
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Leads")

# FIX #6: Use setdefault to ensure session state variables exist without overwriting them
if 'processed_businesses' not in st.session_state:
    st.session_state.processed_businesses = set()

if 'sheets_connection' not in st.session_state:
    st.session_state.sheets_connection = None

if 'failed_rows' not in st.session_state:
    st.session_state.failed_rows = []

def safe_request(url, retries=3, initial_delay=1, max_delay=15):
    """Make a request with exponential backoff retry logic for API errors"""
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            
            # FIX #2: Better handling of API error status codes
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:  # Rate limit exceeded
                backoff_delay = min(initial_delay * (2 ** attempt), max_delay)
                # FIX #3: Add jitter to prevent synchronized retries
                backoff_delay = backoff_delay * (0.8 + 0.4 * random.random())
                logger.warning(f"Rate limit exceeded. Backing off for {backoff_delay:.2f} seconds...")
                time.sleep(backoff_delay)
            elif response.status_code in [400, 403]:
                logger.error(f"API request rejected with status {response.status_code}: {response.text}")
                return None  # Don't retry client errors
            elif response.status_code >= 500:
                backoff_delay = min(initial_delay * (2 ** attempt), max_delay)
                backoff_delay = backoff_delay * (0.8 + 0.4 * random.random())
                logger.warning(f"Server error {response.status_code}. Retrying in {backoff_delay:.2f} seconds ({attempt+1}/{retries})...")
                time.sleep(backoff_delay)
            else:
                backoff_delay = min(initial_delay * (2 ** attempt), max_delay)
                backoff_delay = backoff_delay * (0.8 + 0.4 * random.random())
                logger.warning(f"Request failed with status {response.status_code}. Retrying in {backoff_delay:.2f} seconds ({attempt+1}/{retries})...")
                time.sleep(backoff_delay)
        except requests.exceptions.RequestException as e:
            backoff_delay = min(initial_delay * (2 ** attempt), max_delay)
            backoff_delay = backoff_delay * (0.8 + 0.4 * random.random())
            logger.error(f"Request error: {e}. Retrying in {backoff_delay:.2f} seconds ({attempt+1}/{retries})...")
            time.sleep(backoff_delay)
    
    logger.error(f"Failed to complete request after {retries} attempts.")
    return None

def safe_append(sheet, row_data, business_name, retries=3, initial_delay=1, max_delay=15):
    """Attempts to append a row with retries in case of an API error."""
    for attempt in range(retries):
        try:
            sheet.append_row(row_data)
            logger.info(f"Successfully added {business_name} to Google Sheets.")
            return True
        except gspread.exceptions.APIError as e:
            backoff_delay = min(initial_delay * (2 ** attempt), max_delay)
            # FIX #3: Add jitter to prevent synchronized retries
            backoff_delay = backoff_delay * (0.8 + 0.4 * random.random())
            error_details = f"Error type: {type(e).__name__}, Error message: {str(e)}"
            logger.warning(f"Google Sheets API Error when adding {business_name}. {error_details}. Retrying in {backoff_delay:.2f} seconds ({attempt+1}/{retries})...")
            time.sleep(backoff_delay)
    
    logger.error(f"Failed to add {business_name} to Google Sheets after {retries} attempts.")
    # Store failed row in session state for potential retry
    st.session_state.failed_rows.append((row_data, business_name))
    
    # FIX #5: Save failed rows to local file for persistence across app restarts
    save_failed_rows_to_file()
    
    return False

# FIX #5: Add functions to save and load failed rows from file
def save_failed_rows_to_file():
    """Save failed rows to a local file for persistence"""
    try:
        # Convert business names to strings to ensure serializability
        serializable_rows = []
        for row_data, business_name in st.session_state.failed_rows:
            serializable_rows.append((row_data, str(business_name)))
            
        with open("failed_rows.json", "w") as f:
            json.dump(serializable_rows, f)
        logger.info(f"Saved {len(serializable_rows)} failed rows to file")
    except Exception as e:
        logger.error(f"Error saving failed rows to file: {e}")

def load_failed_rows_from_file():
    """Load failed rows from local file if it exists"""
    try:
        if os.path.exists("failed_rows.json"):
            with open("failed_rows.json", "r") as f:
                loaded_rows = json.load(f)
                
            # Only load if session state is empty to avoid duplicates
            if not st.session_state.failed_rows:
                st.session_state.failed_rows = loaded_rows
                logger.info(f"Loaded {len(loaded_rows)} failed rows from file")
    except Exception as e:
        logger.error(f"Error loading failed rows from file: {e}")

# FIX #4: Optimize API calls by requesting more fields in the initial search
def get_businesses(industries, locations, region):
    businesses = []
    
    # FIX #1: Proper API key validation - check for both None and empty string
    if not GOOGLE_API_KEY:
        st.error("Google API Key not found or empty. Please check your .env file.")
        return []
    
    for location in locations:
        for industry in industries:
            # Add progress indicator
            progress_text = st.empty()
            progress_text.text(f"Searching for {industry} in {location}, {region}...")
            
            # URL encode the query parameters
            encoded_query = urllib.parse.quote_plus(f"{industry} in {location} {region}")
            
            # FIX #4: Request more fields in the initial API call to reduce need for detail requests
            fields = "place_id,name,formatted_address,rating,opening_hours,formatted_phone_number,website,url"
            url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={encoded_query}&key={GOOGLE_API_KEY}&fields={fields}"
            
            data = safe_request(url)
            
            if data is None:
                st.error(f"Error fetching data for {industry} in {location}")
                continue
            
            # FIX #9: Simplify checking for results
            if data.get("results"):
                # Use session state as a set for efficient lookup
                processed_set = st.session_state.processed_businesses
                
                for place in data["results"]:
                    # Skip if we've already processed this place
                    place_id = place.get('place_id', '')
                    if place_id in processed_set:
                        continue
                    
                    # Add to processed set
                    processed_set.add(place_id)
                    
                    # FIX #4: Only fetch additional details if necessary
                    # Check if we have all the fields we need from the initial request
                    if not all(key in place for key in ['formatted_phone_number', 'website']):
                        # Fetch place details with exponential backoff
                        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,formatted_address,formatted_phone_number,website,opening_hours,url&key={GOOGLE_API_KEY}"
                        details_response = safe_request(details_url)
                        
                        if details_response is None:
                            st.error(f"Error fetching details for {place.get('name', 'Unknown')}")
                            # Continue with partial data instead of skipping entirely
                            details = {}
                        else:
                            details = details_response.get("result", {})
                    else:
                        details = place
                    
                    # Merge data from both sources
                    combined_data = {**place, **(details or {})}
                    
                    website = combined_data.get("website", "N/A")
                    social_links = extract_social_media(website)
                    
                    # Get opening hours safely
                    opening_hours = ", ".join(
                        combined_data.get("opening_hours", {}).get("weekday_text", [])
                    ) if "opening_hours" in combined_data else "N/A"
                    
                    businesses.append({
                        "name": combined_data.get("name", "N/A"),
                        "address": combined_data.get("formatted_address", "N/A"),
                        "google_maps_url": combined_data.get("url", f"https://www.google.com/maps/place/?q=place_id:{place_id}"),
                        "business_type": industry,
                        "rating": combined_data.get("rating", "N/A"),
                        "phone_number": combined_data.get("formatted_phone_number", "N/A"),
                        "website": website,
                        "facebook": social_links.get("facebook", "N/A"),
                        "instagram": social_links.get("instagram", "N/A"),
                        "twitter": social_links.get("twitter", "N/A"),
                        "linkedin": social_links.get("linkedin", "N/A"),
                        "tiktok": social_links.get("tiktok", "N/A"),
                        "opening_hours": opening_hours
                    })
            else:
                logger.warning(f"No businesses found for '{industry}' in '{location}'.")
                st.warning(f"No businesses found for '{industry}' in '{location}'.")
            
            progress_text.empty()

    return businesses

# FIX #8: Improve social media extraction with better error handling
def extract_social_media(website_url):
    social_links = {
        "facebook": "N/A",
        "instagram": "N/A",
        "twitter": "N/A", 
        "linkedin": "N/A",
        "tiktok": "N/A"
    }
    
    if not website_url or website_url == "N/A":
        return social_links
    
    # Validate URL format to prevent request errors
    if not website_url.startswith(('http://', 'https://')):
        website_url = 'https://' + website_url
    
    try:
        # Set headers to mimic a browser request to avoid being blocked
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        
        # Reduced timeout and don't follow redirects to avoid hanging on problematic sites
        response = requests.get(website_url, timeout=5, headers=headers, allow_redirects=True)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find links containing social media patterns
            links = soup.find_all('a', href=True)
            
            # Store only the first valid link for each platform
            for link in links:
                href = link['href'].lower()
                
                # Only store the first valid link for each platform
                if "facebook.com" in href and social_links["facebook"] == "N/A":
                    social_links["facebook"] = link['href']
                elif "instagram.com" in href and social_links["instagram"] == "N/A":
                    social_links["instagram"] = link['href']
                elif ("twitter.com" in href or "x.com" in href) and social_links["twitter"] == "N/A":
                    social_links["twitter"] = link['href']
                elif "linkedin.com" in href and social_links["linkedin"] == "N/A":
                    social_links["linkedin"] = link['href']
                elif "tiktok.com" in href and social_links["tiktok"] == "N/A":
                    social_links["tiktok"] = link['href']
        else:
            logger.warning(f"Failed to access {website_url}: HTTP status code {response.status_code}")
    except requests.exceptions.SSLError:
        # Try with HTTP if HTTPS fails
        try:
            http_url = website_url.replace('https://', 'http://')
            logger.info(f"SSL error with {website_url}, trying HTTP: {http_url}")
            response = requests.get(http_url, timeout=5)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                # Process links (same code as above)
                links = soup.find_all('a', href=True)
                for link in links:
                    href = link['href'].lower()
                    if "facebook.com" in href and social_links["facebook"] == "N/A":
                        social_links["facebook"] = link['href']
                    elif "instagram.com" in href and social_links["instagram"] == "N/A":
                        social_links["instagram"] = link['href']
                    elif ("twitter.com" in href or "x.com" in href) and social_links["twitter"] == "N/A":
                        social_links["twitter"] = link['href']
                    elif "linkedin.com" in href and social_links["linkedin"] == "N/A":
                        social_links["linkedin"] = link['href']
                    elif "tiktok.com" in href and social_links["tiktok"] == "N/A":
                        social_links["tiktok"] = link['href']
        except Exception as e:
            logger.warning(f"HTTP fallback failed for {website_url}: {e}")
    except requests.exceptions.ConnectionError:
        logger.warning(f"Connection error for {website_url}")
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout when accessing {website_url}")
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error accessing {website_url}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error extracting social media from {website_url}: {e}")
    
    return social_links

# FIX #7: Improve Google Sheets authentication handling
def connect_to_google_sheets():
    # Check if we already have a connection in session state
    if st.session_state.sheets_connection is not None:
        logger.info("Using existing Google Sheets connection from session state")
        return st.session_state.sheets_connection
    
    try:
        # Check if token file exists
        if not os.path.exists("token.json"):
            st.error("Authentication file (token.json) not found.")
            # FIX #7: Provide guidance instead of stopping execution
            st.markdown("""
            ### Authentication Setup Instructions:
            1. Run the Google OAuth setup script to generate your token.json file.
            2. Place the token.json file in the same directory as this app.
            3. Restart the app after completing these steps.
            """)
            # Return None instead of stopping execution
            return None
            
        creds = Credentials.from_authorized_user_file(
            "token.json", 
            ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        
        # Check if token is expired and handle refresh properly
        if creds.expired and hasattr(creds, 'refresh_token') and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save refreshed credentials
                with open("token.json", "w") as token:
                    token.write(creds.to_json())
                logger.info("Successfully refreshed authentication token.")
            except RefreshError as e:
                logger.error(f"Failed to refresh authentication token: {e}")
                st.error("Authentication token has expired and cannot be refreshed.")
                st.markdown("""
                ### Re-authentication Instructions:
                1. Delete the existing token.json file
                2. Run the authentication script again to generate a new token
                3. Restart the app after completing these steps
                """)
                return None
        elif creds.expired:
            logger.error("Authentication token has expired and no refresh token is available.")
            st.error("Authentication token has expired and no refresh token is available.")
            st.markdown("""
            ### Re-authentication Instructions:
            1. Delete the existing token.json file
            2. Run the authentication script again to generate a new token
            3. Restart the app after completing these steps
            """)
            return None
        
        # Authenticate with Google Sheets
        client = gspread.authorize(creds)
        
        try:
            # Explicitly open the spreadsheet and select the "Leads" tab
            spreadsheet = client.open(SPREADSHEET_NAME)  # Open the spreadsheet
            sheet = spreadsheet.worksheet("Leads")  # Open the specific sheet tab
            
            logger.info(f"Successfully connected to Google Sheet: {SPREADSHEET_NAME}, Tab: Leads")
            
            # Store in session state for reuse
            st.session_state.sheets_connection = sheet
            return sheet
        
        except gspread.exceptions.WorksheetNotFound:
            logger.error(f"Worksheet 'Leads' not found in spreadsheet '{SPREADSHEET_NAME}'.")
            st.error(f"Worksheet 'Leads' not found in spreadsheet '{SPREADSHEET_NAME}'.")
            st.markdown("""
            Please create a worksheet named 'Leads' in your spreadsheet.
            1. Open your Google Spreadsheet
            2. Add a new tab named 'Leads'
            3. Refresh this app
            """)
            return None
        
        except gspread.exceptions.SpreadsheetNotFound:
            logger.error(f"Spreadsheet '{SPREADSHEET_NAME}' not found")
            st.error(f"Spreadsheet '{SPREADSHEET_NAME}' not found.")
            st.markdown(f"""
            Please check your .env configuration or create a spreadsheet with the name '{SPREADSHEET_NAME}'.
            1. Open Google Sheets
            2. Create a new spreadsheet named '{SPREADSHEET_NAME}'
            3. Add a worksheet named 'Leads'
            4. Refresh this app
            """)
            return None
        
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API Error: {str(e)}")
            st.error(f"Google Sheets API Error: {str(e)}")
            return None

    except Exception as e:
        logger.error(f"Error connecting to Google Sheets: {str(e)}")
        st.error(f"Error connecting to Google Sheets: {str(e)}")
        return None

# FIX #10: Improved retry functionality for failed rows
def retry_failed_rows():
    if not st.session_state.failed_rows:
        st.info("No failed rows to retry.")
        return
    
    failed_rows = st.session_state.failed_rows.copy()
    # Clear the list first to avoid duplicate entries if retries fail again
    st.session_state.failed_rows = []
    
    sheet = connect_to_google_sheets()
    if not sheet:
        st.error("Cannot retry without Google Sheets connection. Please fix authentication issues first.")
        # Restore the failed rows since we couldn't process them
        st.session_state.failed_rows = failed_rows
        return
    
    success_count = 0
    
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    for i, (row_data, business_name) in enumerate(failed_rows):
        progress = (i + 1) / len(failed_rows)
        progress_bar.progress(progress)
        status_text.text(f"Retrying {i+1}/{len(failed_rows)}: {business_name}")
        
        # Try to append with fresh retry counter
        success = safe_append(sheet, row_data, business_name)
        
        if success:
            success_count += 1
    
    if st.session_state.failed_rows:
        status_text.text(f"Retry completed. Successfully added {success_count}/{len(failed_rows)} previously failed rows. {len(st.session_state.failed_rows)} rows still failed.")
        st.warning(f"{len(st.session_state.failed_rows)} rows still failed to add.")
    else:
        status_text.text(f"Retry completed. Successfully added all {success_count} previously failed rows!")
        st.success("Successfully added all previously failed rows!")
        # Update the local file to reflect empty failed rows
        save_failed_rows_to_file()

# Team Members
team_members = ["Allan", "Arnis", "Matt", "Stan", "James", "Kyle", "Bailey", "Martin", "Diogo"]

# Dictionary of regions with corresponding locations
regions = {
    "Northern Ireland": [
        "Belfast", "Lisburn", "Newry", "Armagh", "Derry", "Antrim", "Bangor",
        "Ballymena", "Coleraine", "Carrickfergus", "Craigavon", "Enniskillen",
        "Larne", "Newtownabbey", "Newtownards", "Ballymoney", "Banbridge",
        "Cookstown", "Downpatrick", "Dungannon", "Holywood", "Limavady",
        "Magherafelt", "Omagh", "Portadown", "Strabane", "Ballyclare",
        "Warrenpoint", "Newcastle", "Ahoghill", "Armoy", "Aughnacloy",
        "Ballycastle", "Ballygally", "Ballyhalbert", "Ballykelly",
        "Ballynahinch", "Bushmills", "Caledon", "Castlederg",
        "Castledawson", "Castlewellan", "Clough", "Cloughey",
        "Crumlin", "Donaghadee", "Draperstown", "Dromore",
        "Fintona", "Garvagh", "Gilford", "Hillsborough",
        "Irvinestown", "Kilkeel", "Killyleagh", "Kesh",
        "Lisnaskea", "Maghera", "Markethill", "Moira",
        "Moneyreagh", "Moneymore", "Portaferry", "Portglenone",
        "Portstewart", "Randalstown", "Rasharkin", "Rostrevor",
        "Saintfield", "Sixmilecross", "Tandragee", "Tempo",
        "Toome", "Whitehead"
    ],

    "Ireland": [
        "Dublin", "Cork", "Limerick", "Galway", "Waterford", "Drogheda", "Dundalk", "Bray",
        "Navan", "Kilkenny", "Ennis", "Carlow", "Tralee", "Newbridge", "Portlaoise", "Balbriggan",
        "Swords", "Clonmel", "Wexford", "Athlone", "Letterkenny", "Mullingar", "Celbridge",
        "Sligo", "Greystones", "Leixlip", "Clondalkin", "Arklow", "Tullamore", "Killarney",
        "Cobh", "Ashbourne", "Midleton", "Longford", "Castlebar", "Ballina", "Carrick-on-Shannon",
        "Nenagh", "Roscommon", "Thurles", "Monaghan", "Gorey", "Mallow", "Kells", "Trim",
        "Carrickmacross", "Westport", "Youghal", "Edenderry", "Portmarnock", "Skerries"
    ],

    "UK": [
        "London", "Manchester", "Birmingham", "Liverpool", "Bristol", "Leeds", "Sheffield",
        "Newcastle", "Cardiff", "Nottingham", "Southampton", "Leicester", "Coventry", "Bradford",
        "Hull", "Stoke-on-Trent", "Derby", "Swansea", "Plymouth", "Reading", "Brighton", "Middlesbrough",
        "Luton", "Bolton", "Bournemouth", "Norwich", "Swindon", "Wolverhampton", "Milton Keynes",
        "Sunderland", "Ipswich", "Blackpool", "Peterborough", "York", "Dudley", "Telford", "Cambridge",
        "Exeter", "Gloucester", "Blackburn", "Maidstone", "Slough", "Poole", "Warrington", "Eastbourne",
        "Colchester", "Basildon", "Crawley", "Newport", "Stockport", "Huddersfield", "Basingstoke",
        "Preston", "Birkenhead", "Gillingham", "Worthing", "Cheltenham", "Lincoln", "Chester", "Bath",
        "Chelmsford", "Hastings", "Solihull", "Southend-on-Sea"
    ],

    "Scotland": [
        "Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness", "Stirling", "Perth",
        "Ayr", "Dumfries", "Falkirk", "Livingston", "Kirkcaldy", "Paisley", "East Kilbride",
        "Cumbernauld", "Hamilton", "Dunfermline", "Greenock", "Kilmarnock", "Coatbridge",
        "Glenrothes", "Airdrie", "Irvine", "Motherwell", "Arbroath", "Elgin", "Dumbarton",
        "Alloa", "St Andrews", "Dunblane", "Forfar", "Oban", "Fort William", "Peebles",
        "Largs", "Jedburgh", "Hawick", "Stornoway", "Thurso", "Wick", "Troon"
    ]
}

# Industry categories & subcategories
industry_categories = {
    "Health & Beauty": [
        "barber_shop", "hair_salon", "beauty_salon", "spa", "pharmacy",
        "dentist", "doctor", "hospital", "physiotherapist", "optician",
        "chiropractor", "nail_salon", "massage_therapist", "tanning_salon"
    ],

    "Hospitality & Food": [
        "restaurant", "cafe", "bar", "bakery", "fast_food", "night_club",
        "catering_service", "ice_cream_shop", "food_truck", "tea_house"
    ],

    "Retail & Shopping": [
        "clothing_store", "shoe_store", "supermarket", "jewelry_store",
        "home_goods_store", "book_store", "florist", "furniture_store",
        "convenience_store", "hardware_store", "pet_store", "shopping_mall",
        "liquor_store", "toy_store", "baby_store", "outdoor_sports_store",
        "second_hand_store", "pawn_shop", "gift_shop", "hobby_shop"
    ],

    "Automotive": [
        "car_dealer", "car_rental", "car_repair", "car_wash", "gas_station",
        "motorcycle_dealer", "auto_parts_store", "tire_shop", "truck_dealer"
    ],

    "Trades & Services": [
        "electrician", "plumber", "locksmith", "moving_company", "roofing_contractor",
        "construction_company", "painter", "pest_control_service", "handyman",
        "carpenter", "gardener", "landscaper", "window_cleaning_service",
        "cleaning_service", "excavation_contractor", "tree_service"
    ],

    "Finance & Professional Services": [
        "accounting", "bank", "insurance_agency", "lawyer",
        "real_estate_agency", "consulting", "marketing_agency",
        "financial_planner", "mortgage_broker", "payday_loan",
        "legal_service", "notary_public", "tax_preparation_service"
    ],

    "Fitness & Recreation": [
        "gym", "stadium", "sports_club", "yoga_studio", "swimming_pool",
        "fitness_center", "martial_arts_school", "personal_trainer",
        "rock_climbing_gym", "boxing_gym", "dance_studio"
    ],

    "Education & Childcare": [
        "school", "university", "library", "tutoring_service",
        "preschool", "child_care", "music_school", "language_school",
        "driving_school", "cooking_school"
    ],

    "Tech & IT Services": [
        "it_services", "computer_store", "electronics_store",
        "web_design", "software_company", "telecommunications_company",
        "cyber_security_firm", "app_development", "data_recovery_service"
    ],

    "Entertainment & Tourism": [
        "museum", "art_gallery", "amusement_park", "casino",
        "travel_agency", "zoo", "movie_theater", "escape_room",
        "bowling_alley", "water_park", "arcade", "circus",
        "karaoke_bar", "theme_park", "tour_operator"
    ],

    "Industrial & Manufacturing": [
        "factory", "warehouse", "metal_fabricator", "printing_service",
        "recycling_center", "chemical_supplier", "engineering_firm",
        "plastics_manufacturer", "packaging_supplier"
    ]
}

# FIX #5: Load any previously failed rows from file on app startup
load_failed_rows_from_file()

# Streamlit Web App
st.title("Business Finder for Notion CRM")
st.write("Find businesses by industry and location, then save them directly to your CRM.")

# Create tabs for different sections
tab1, tab2, tab3 = st.tabs(["Search Businesses", "Failed Jobs", "App Settings"])

with tab1:
    col1, col2 = st.columns(2)
    
    with col1:
        selected_category = st.selectbox("Select Industry Category", list(industry_categories.keys()))
        selected_industries = st.multiselect("Select Industries", industry_categories[selected_category])
    
    with col2:
        # Select region first
        selected_region = st.selectbox("Select Region", list(regions.keys()))
        # Dynamically update available locations based on selected region
        selected_locations = st.multiselect("Select Locations", regions[selected_region])
    
    assigned_to = st.selectbox("Assign to Team Member", team_members)
    
    if st.button("Find Businesses"):
        # FIX #1: Validate API key before proceeding
        if not GOOGLE_API_KEY:
            st.error("Google API Key not found. Please check your .env file.")
        elif not selected_industries:
            st.error("Please select at least one industry")
        elif not selected_locations:
            st.error("Please select at least one location")
        else:
            with st.spinner("Fetching businesses..."):
                all_businesses = get_businesses(selected_industries, selected_locations, selected_region)
                
                if all_businesses:
                    st.success(f"Found {len(all_businesses)} businesses!")
                    
                    # Convert to DataFrame for display
                    import pandas as pd
                    df = pd.DataFrame(all_businesses)
                    
                    # Show preview table with all columns
                    st.subheader("Business Preview")
                    st.dataframe(df)  # Displays full preview instead of selected columns

                    # Button to save businesses to Google Sheets
                    if st.button("Save All to Google Sheets"):
                        st.info("Preparing to save businesses to Google Sheets. Please wait...")
                        
                        with st.spinner("Saving businesses to Google Sheets..."):
                            try:
                                # Connect to Google Sheets
                                sheet = connect_to_google_sheets()
                                
                                if not sheet:
                                    st.error("❌ Could not connect to Google Sheets.")
                                else:
                                    st.write("✅ Successfully connected to Google Sheets.")  # Debug message
                                    st.write(f"📝 Spreadsheet Name: {sheet.spreadsheet.title}")
                                    st.write(f"📄 Worksheet Tab: {sheet.title}")
                    
                                    # ✅ DEBUG TEST ROW
                                    test_row = ["DEBUG", "row", "test", "from", "Streamlit"]
                                    try:
                                        sheet.append_row(test_row)
                                        st.success("✅ [DEBUG] Test row appended successfully.")
                                    except Exception as e:
                                        st.error(f"❌ [DEBUG] Failed to append test row: {str(e)}")
                    
                                    # 🚀 Proceed with real saving if test passed
                                    success_count = 0
                                    failures = []
                                    status_text = st.empty()
                    
                                    if HAVE_STQDM:
                                        # Use stqdm for non-blocking progress tracking
                                        for business in stqdm(all_businesses, desc="Saving to Google Sheets"):
                                            business_name = business["name"]
                                            row_data = list(business.values()) + [assigned_to]
                    
                                            st.write(f"📍 Attempting to save: {business_name}")  # Debug message
                                            success = safe_append(sheet, row_data, business_name)
                    
                                            if success:
                                                success_count += 1
                                            else:
                                                failures.append(business_name)
                                    else:
                                        st.warning("For better performance, install stqdm: pip install stqdm")
                                        st.info("Falling back to standard method...")
                    
                                        progress_bar = st.progress(0)
                    
                                        for i, business in enumerate(all_businesses):
                                            business_name = business["name"]
                                            row_data = list(business.values()) + [assigned_to]
                    
                                            progress = (i + 1) / len(all_businesses)
                                            progress_bar.progress(progress)
                                            status_text.text(f"Processing: {i+1}/{len(all_businesses)} - {business_name}")
                    
                                            st.write(f"📍 Attempting to save: {business_name}")  # Debug message
                                            success = safe_append(sheet, row_data, business_name)
                    
                                            if success:
                                                success_count += 1
                                            else:
                                                failures.append(business_name)
                    
                                    # Final status update
                                    if failures:
                                        status_text.text(f"❌ Completed with some failures. Added {success_count} of {len(all_businesses)} businesses.")
                                        st.error(f"❌ Failed to add these businesses: {', '.join(failures[:5])}{' and more...' if len(failures) > 5 else ''}")
                                        logger.error(f"Failed to add these businesses: {failures}")
                                    else:
                                        status_text.text(f"✅ Completed! Successfully added all {success_count} businesses.")
                                        st.balloons()
                                        st.success(f"✅ Successfully added {success_count} businesses to your CRM!")
                    
                            except Exception as e:
                                st.error(f"⚠️ An error occurred while saving to Google Sheets: {str(e)}")
                                logger.error(f"Error while saving to Google Sheets: {str(e)}")

with tab2:
    st.subheader("Failed Jobs")
    
    if st.session_state.failed_rows:
        st.warning(f"There are {len(st.session_state.failed_rows)} failed business entries that weren't saved to Google Sheets.")
        
        # Display the first few failed businesses
        if len(st.session_state.failed_rows) > 0:
            st.write("Failed businesses:")
            for i, (_, business_name) in enumerate(st.session_state.failed_rows[:5]):
                st.write(f"{i+1}. {business_name}")
            
            if len(st.session_state.failed_rows) > 5:
                st.write(f"... and {len(st.session_state.failed_rows) - 5} more.")
            
            if st.button("Retry Failed Jobs"):
                retry_failed_rows()
    else:
        st.success("No failed jobs to display.")

with tab3:
    st.subheader("App Settings")
    
    # Display current settings
    st.write("Current Settings:")
    st.write(f"- Google API Key: {'Configured' if GOOGLE_API_KEY else 'Not Configured'}")
    st.write(f"- Spreadsheet Name: {SPREADSHEET_NAME}")
    st.write(f"- Progress Tracking: {'Using stqdm (recommended)' if HAVE_STQDM else 'Using standard progress (consider installing stqdm)'}")
    
    # Instructions for setup
    st.subheader("Setup Instructions")
    st.write("""
    1. Create a .env file in the same directory as this app with the following:
       ```
       GOOGLE_API_KEY=your_google_api_key
       SPREADSHEET_NAME=Leads
       ```
    2. Make sure you have a Google OAuth [`token.json`](token.json ) file for Sheet access
    3. Install required packages: [`pip install streamlit requests gspread google-auth python-dotenv beautifulsoup4 stqdm`](app.py )
    """)
    
    # Team members management
    st.subheader("Team Members")
    new_member = st.text_input("Add Team Member")
    if st.button("Add") and new_member:
        team_members.append(new_member)
        st.success(f"Added {new_member} to team members list!")
    
    # FIX #10: Add confirmation step before clearing session state
    if st.button("Clear Session State (Reset App)"):
        confirm = st.checkbox("I understand this will reset all app data", value=False)
        if confirm and st.button("Confirm Reset"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.success("Session state cleared! Refreshing...")
            st.experimental_rerun()
        elif not confirm:
            st.warning("Please confirm that you want to reset the app data")