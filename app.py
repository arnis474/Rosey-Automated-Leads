import streamlit as st
import requests
import json
import os
import re
import gspread
import time
import urllib.parse
import random
import logging  # For detailed logging from our utils functions
import pandas as pd  # To display results nicely in a table
from utils.api_utils import make_api_request_with_retry, PLACES_API_ENDPOINT_DETAILS  # For the modified get_place_details
from utils.google_utils import get_coordinates
from utils.radius_utils import generate_grid_points, perform_grid_search
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from pathlib import Path  # NEW: to help resolve .env path

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

# --- Updated .env loading ---
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or "AIzaSyAsM1m80IgYQ-042GdiwhnlWg025j-ozg0"
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Leads")

# Debugging output to confirm values loaded correctly
print("ENV file loaded from:", env_path)
print("GOOGLE_API_KEY:", GOOGLE_API_KEY)

# Log whether keys were loaded
logger.info(f"GOOGLE_API_KEY loaded: {'Yes' if GOOGLE_API_KEY else 'No'}")
logger.info(f"SPREADSHEET_NAME: {SPREADSHEET_NAME}")

# Stop app early if key is missing
if not GOOGLE_API_KEY:
    st.error("❌ Google API Key not loaded. Please check your `.env` file or Streamlit `secrets.toml` and restart the app.")
    st.stop()

# Ensure session state variables are initialized
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

# UPDATED get_businesses function with Synonym Expansion

# FINAL UPDATED get_businesses function
# Handles new arguments: search_target (list or string), grid_cell_radius_km, region (optional)
# Includes synonym expansion, pagination, and grid fallback logic.

def get_businesses(industries, search_target, grid_cell_radius_km, region=None):
    """
    Fetches businesses using Google Places Text Search, handling pagination,
    synonym expansion, and conditional grid search fallback.

    Args:
        industries (list): List of industry keywords selected by the user.
        search_target (str or list): EITHER a single location string (manual input)
                                     OR a list of location strings (dropdown selection).
        grid_cell_radius_km (float): The radius (in km) for grid search cells, from the slider.
        region (str, optional): The region context, provided only if using dropdowns. Defaults to None.

    Returns:
        list: A list of unique business dictionaries found. Returns empty list on error.
    """
    businesses = [] # Final list of processed business dicts

    # --- Initial Checks ---
    if not GOOGLE_API_KEY:
        st.error("Google API Key not found or empty. Please check your .env file.")
        # Returning [] instead of None to be consistent with other error returns below
        return []
    if not industries:
        st.warning("No industries selected for search.")
        return []
    if not search_target:
        st.warning("No search target (location/list) provided.")
        return []

    # Ensure necessary functions and dictionaries are globally accessible (add more checks if needed)
    # These checks help prevent NameError if functions aren't defined before get_businesses
    global INDUSTRY_SYNONYMS, safe_request, get_coordinates, generate_grid_points, perform_grid_search, extract_social_media
    if 'INDUSTRY_SYNONYMS' not in globals(): logger.error("INDUSTRY_SYNONYMS missing"); return []
    if 'safe_request' not in globals(): logger.error("safe_request missing"); return []
    if 'get_coordinates' not in globals(): logger.error("get_coordinates missing"); return []
    if 'generate_grid_points' not in globals(): logger.error("generate_grid_points missing"); return []
    if 'perform_grid_search' not in globals(): logger.error("perform_grid_search missing"); return []
    if 'extract_social_media' not in globals(): logger.error("extract_social_media missing"); return []

    # --- Determine Locations to Process (Handles list or string input) ---
    if isinstance(search_target, str):
        locations_to_process = [search_target] # Treat manual input as a list with one item
        logger.info(f"Processing manual location target: '{search_target}'")
    elif isinstance(search_target, list):
        locations_to_process = search_target
        logger.info(f"Processing location list target: {locations_to_process} (Region context: {region})")
    else:
        st.error(f"Invalid search target type received: {type(search_target)}")
        logger.error(f"Invalid search_target type: {type(search_target)}")
        return []

    # --- Convert Slider Radius to Meters (for Grid Search) ---
    grid_cell_radius_meters_dynamic = grid_cell_radius_km * 1000
    logger.info(f"Using dynamic grid cell radius: {grid_cell_radius_meters_dynamic:.0f}m")
    # Keep overall city coverage fixed for now
    CITY_COVERAGE_RADIUS_METERS = 15000 # Example: Fixed 15km coverage

    # Get the master processed set reference ONCE
    processed_set = st.session_state.processed_businesses

    # --- Main Loop: Locations -> Industries -> Keywords (Synonyms) ---
    for location in locations_to_process: # Loop through the determined list of locations
        for industry in industries: # ORIGINAL industry term selected by user
            progress_text = st.empty()
            progress_text.text(f"Starting search for '{industry}' and variants in '{location}'...")
            logger.info(f"=== Processing Original Industry: '{industry}' in Location: '{location}' ===")

            # --- Synonym Expansion ---
            search_keywords = [industry]
            if industry in INDUSTRY_SYNONYMS:
                variants = INDUSTRY_SYNONYMS[industry]
                search_keywords.extend(variants)
                logger.info(f"Expanded '{industry}' to search for: {search_keywords}")
            # else: logger.info(f"No defined synonyms for '{industry}'.") # Less verbose

            all_results_for_industry_and_variants = [] # Accumulate results across keywords

            # --- Inner Loop: Keyword Variants ---
            for current_keyword in search_keywords:
                logger.info(f"--- Performing search for Keyword Variant: '{current_keyword}' (Original: '{industry}') in '{location}' ---")
                progress_text.text(f"Searching for '{current_keyword}' in '{location}'...")

                # --- Adapt Query based on Input Mode ---
                if isinstance(search_target, str): # Manual mode
                     # Use keyword + manual location string (which is 'location' in this loop)
                     encoded_query = urllib.parse.quote_plus(f"{current_keyword} near {location}")
                else: # List mode (search_target was a list)
                     # Use keyword + location from list + region context (if available)
                     query_text = f"{current_keyword} in {location}" + (f" {region}" if region else "")
                     encoded_query = urllib.parse.quote_plus(query_text)

                fields = "place_id,name,formatted_address,rating,opening_hours,formatted_phone_number,website,url"
                url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={encoded_query}&key={GOOGLE_API_KEY}&fields={fields}"

                # --- Pagination Logic ---
                all_page_results = [] # Results for THIS keyword's paginated/grid search
                current_page = 0
                MAX_PAGES_TO_FETCH = 3
                next_page_token = None
                logger.info(f"Fetching page {current_page + 1} for '{current_keyword}' query...")
                current_url = url
                data = safe_request(current_url)

                if data is None:
                    st.error(f"Initial API request failed for keyword '{current_keyword}' in '{location}'. Skipping this keyword.")
                    logger.error(f"Initial safe_request failed for keyword '{current_keyword}', URL: {current_url}")
                    continue # Skip to next keyword variant

                page_results = data.get("results", [])
                all_page_results.extend(page_results)
                next_page_token = data.get("next_page_token")
                current_page += 1
                logger.info(f"Page 1: Found {len(page_results)} results. next_page_token: {'Yes' if next_page_token else 'No'}")

                while next_page_token and current_page < MAX_PAGES_TO_FETCH:
                    logger.info(f"Found next_page_token. Waiting 2 seconds before fetching page {current_page + 1}...")
                    time.sleep(2)
                    next_page_url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?pagetoken={next_page_token}&key={GOOGLE_API_KEY}"
                    logger.info(f"Fetching page {current_page + 1}...")
                    data = safe_request(next_page_url)
                    if data is None:
                        st.warning(f"API request failed for page {current_page + 1}. Proceeding for '{current_keyword}'.")
                        logger.error(f"safe_request failed for pagination URL: {next_page_url}")
                        break
                    page_results = data.get("results", [])
                    all_page_results.extend(page_results)
                    next_page_token = data.get("next_page_token")
                    current_page += 1
                    logger.info(f"Page {current_page}: Found {len(page_results)} results. next_page_token: {'Yes' if next_page_token else 'No'}")
                    if not page_results and next_page_token: logger.warning("Received next_page_token but no results..."); break
                logger.info(f"Finished pagination for '{current_keyword}'. Potential results before grid: {len(all_page_results)}")
                # --- Pagination Logic END ---

                # --- Grid Search Fallback ---
                GRID_SEARCH_THRESHOLD = 55

                if len(all_page_results) < GRID_SEARCH_THRESHOLD:
                    logger.warning(f"Paginated search for '{current_keyword}' found only {len(all_page_results)} results (<{GRID_SEARCH_THRESHOLD}). Initiating grid search...")
                    st.write(f"  Expanding search for '{current_keyword}' with Grid...")

                    # --- Adapt Geocoding based on Input Mode ---
                    if isinstance(search_target, str): # Manual mode
                        location_for_geocoding = location # Geocode the manual input directly
                    else: # List mode
                        location_for_geocoding = f"{location}, {region}" # Use location + region context

                    center_lat, center_lon = get_coordinates(location_for_geocoding, GOOGLE_API_KEY)

                    if center_lat is not None and center_lon is not None:
                        # Generate grid points using DYNAMIC cell radius
                        grid_points = generate_grid_points(
                            center_lat, center_lon,
                            CITY_COVERAGE_RADIUS_METERS,
                            grid_cell_radius_meters_dynamic # Use dynamic radius
                        )
                        if grid_points:
                            st.write(f"    Generated {len(grid_points)} points for grid search for '{current_keyword}'.")
                            # Perform grid search using DYNAMIC cell radius
                            grid_results = perform_grid_search(
                                industry_keyword=current_keyword,
                                grid_points=grid_points,
                                search_radius_meters=grid_cell_radius_meters_dynamic, # Use dynamic radius
                                processed_businesses_set=processed_set, # Pass master set
                                safe_request_func=safe_request,
                                api_key=GOOGLE_API_KEY
                            )
                            if grid_results:
                                 new_grid_count = len(grid_results)
                                 logger.info(f"Grid search found {new_grid_count} additional unique leads for '{current_keyword}'.")
                                 st.write(f"    Grid search added {new_grid_count} new unique leads.")
                                 all_page_results.extend(grid_results) # Add grid results
                                 logger.info(f"Total results for '{current_keyword}' after merging grid: {len(all_page_results)}")
                            else:
                                 logger.info("Grid search did not find any additional unique leads for this keyword.")
                                 st.write("    Grid search found no additional new leads for this keyword.")
                        else:
                             logger.warning("Grid point generation failed.")
                             st.write("    Grid point generation failed.")
                    else:
                        logger.error(f"Could not get coordinates for {location_for_geocoding}. Skipping grid search for '{current_keyword}'.")
                        st.error(f"    Could not get coordinates. Skipping grid search for '{current_keyword}'.")
                else:
                    logger.info(f"Paginated search for '{current_keyword}' found enough results (>= {GRID_SEARCH_THRESHOLD}). Skipping grid search.")
                # --- Grid Search Fallback END ---

                # Accumulate results for THIS keyword variant
                all_results_for_industry_and_variants.extend(all_page_results)
                # logger.debug(...) # Reduce noise, info log below is better

            # --- End Inner Loop: 'for current_keyword in search_keywords:' ---

            logger.info(f"Accumulated {len(all_results_for_industry_and_variants)} potential results total for original industry '{industry}' in '{location}'.")

            # --- Process Combined Results (for original industry + variants) ---
            progress_text.text(f"Processing {len(all_results_for_industry_and_variants)} results for '{industry}' in '{location}'...")
            if all_results_for_industry_and_variants:
                logger.info(f"Processing all {len(all_results_for_industry_and_variants)} results gathered...")
                # processed_set = st.session_state.processed_businesses # Already got reference above
                ids_added_this_industry_block = set()

                for place in all_results_for_industry_and_variants:
                    place_id = place.get('place_id', '')
                    if place_id and place_id not in processed_set and place_id not in ids_added_this_industry_block:
                        processed_set.add(place_id)
                        ids_added_this_industry_block.add(place_id)

                        # --- Conditional Details Call ---
                        if not all(key in place for key in ['formatted_phone_number', 'website']):
                            details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,formatted_address,formatted_phone_number,website,opening_hours,url&key={GOOGLE_API_KEY}"
                            details_response = safe_request(details_url)
                            details = details_response.get("result", {}) if details_response else {}
                            if not details: logger.error(f"Error fetching details for {place.get('name', 'Unknown')}")
                        else:
                            details = place

                        # --- Merge data, Extract Socials, Format Output ---
                        combined_data = {**place, **(details or {})}
                        website = combined_data.get("website", "N/A")
                        social_links = extract_social_media(website)
                        opening_hours = ", ".join(combined_data.get("opening_hours", {}).get("weekday_text", [])) if combined_data.get("opening_hours") else "N/A"

                        businesses.append({
                            "name": combined_data.get("name", "N/A"),
                            "address": combined_data.get("formatted_address", "N/A"),
                            "Maps_url": combined_data.get("url", f"https://www.google.com/maps/place/?q=place_id:{place_id}"), # Correct Maps URL
                            "business_type": industry, # Use ORIGINAL industry
                            "rating": combined_data.get("rating", "N/A"),
                            "phone_number": combined_data.get("formatted_phone_number", "N/A"),
                            "website": website,
                            "facebook": social_links.get("facebook", "N/A"),
                            "instagram": social_links.get("instagram", "N/A"),
                            "twitter": social_links.get("twitter", "N/A"),
                            "linkedin": social_links.get("linkedin", "N/A"),
                            "tiktok": social_links.get("tiktok", "N/A"),
                            "opening_hours": opening_hours,
                            "place_id": place_id
                        })
                    # End if place_id is new
                # End loop 'for place in ...'
                logger.info(f"Finished processing variants for '{industry}'. Added {len(ids_added_this_industry_block)} new unique businesses to final list.")
            else:
                 logger.warning(f"No businesses found for '{industry}' or its variants in '{location}'.")

            progress_text.empty() # Clear progress text

        # End loop 'for industry in industries:'
    # End loop 'for location in locations_to_process:'

    # --- Sorting Results (Optional - Skipped as per user request) ---
    # ...

    logger.info(f"get_businesses finished. Total unique businesses collected: {len(businesses)}")
    return businesses # Return the final list

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

# Industry categories & subcategories (Expanded)
industry_categories = {
    "Health & Beauty": [
        "barber_shop", "hair_salon", "beauty_salon", "spa", "pharmacy",
        "dentist", "doctor", "hospital", "physiotherapist", "optician",
        "chiropractor", "nail_salon", "massage_therapist", "tanning_salon",
        "veterinary_care", # Added
        "hearing_aid_store", # Added
        "cosmetic_surgery" # Added
    ],

    "Hospitality & Food": [
        "restaurant", "cafe", "bar", "pub", # Added pub as common term
        "bakery", "fast_food", "meal_takeaway", # Added meal_takeaway
        "night_club", "catering_service", "ice_cream_shop", "food_truck",
        "tea_house", "lodging", "hotel", "motel", # Added lodging/hotel/motel
        "grocery_store", "supermarket" # Added grocery_store
    ],

    "Retail & Shopping": [
        "clothing_store", "shoe_store", "department_store", # Added department_store
        "supermarket", "grocery_store", # Added grocery_store here too for overlap
        "jewelry_store", "home_goods_store", "book_store", "florist",
        "furniture_store", "convenience_store", "hardware_store",
        "pet_store", "shopping_mall", "liquor_store", "toy_store",
        "baby_store", "bicycle_store", # Added bicycle_store
        "electronics_store", # Keeping here, though could be Tech
        "mobile_phone_store", # Added
        "garden_center", # Added
        "music_store", # Added
        "video_game_store", # Added
        "outdoor_sports_store", "second_hand_store", "pawn_shop",
        "gift_shop", "hobby_shop"
    ],

    "Automotive": [
        "car_dealer", "car_rental", "car_repair", "car_wash", "gas_station",
        "motorcycle_dealer", "auto_parts_store", "tire_shop", "truck_dealer",
        "rv_dealer" # Added
        # parking omitted as likely less relevant for lead gen
    ],

    "Trades & Services": [
        "electrician", "plumber", "locksmith", "roofing_contractor",
        "general_contractor", # Added
        "hvac_contractor", # Added
        "construction_company", "painter", "pest_control_service", "handyman",
        "carpenter", "gardener", "landscaper", "window_cleaning_service",
        "cleaning_service", "laundry", # Added
        "storage", # Added self-storage
        "security_systems", # Added
        "excavation_contractor", "tree_service"
    ],

    "Finance & Professional Services": [
        "accounting", "bank", "insurance_agency", "lawyer", "architect", # Added architect
        "real_estate_agency", # Kept here, also see Real Estate category
        "consulting", "marketing_agency", "employment_agency", # Added employment_agency
        "financial_planner", "mortgage_broker", "payday_loan",
        "legal_service", "notary_public", "tax_preparation_service",
        "photographer" # Added
    ],

    "Fitness & Recreation": [
        "gym", "stadium", "sports_club", "yoga_studio", "swimming_pool",
        "fitness_center", "martial_arts_school", "personal_trainer",
        "rock_climbing_gym", "boxing_gym", "dance_studio",
        "golf_course" # Added
    ],

    "Education & Childcare": [
        "school", "university", "library", "tutoring_service",
        "preschool", "child_care", "music_school", "language_school",
        "driving_school", "cooking_school", "art_school", # Added
        "trade_school" # Added
    ],

    "Tech & IT Services": [
        "it_services", "computer_store", "electronics_store", # Kept here
        "computer_repair", # Added
        "web_design", "software_company", "telecommunications_company",
        "cyber_security_firm", "app_development", "data_recovery_service"
    ],

    "Entertainment & Tourism": [
        "museum", "art_gallery", "amusement_park", "casino",
        "travel_agency", "zoo", "aquarium", # Added aquarium
        "movie_theater", "cinema", # Added cinema
        "stadium", # Added here too/instead of Fitness
        "night_club", # Added here too/instead of Hospitality
        "event_venue", # Added
        "wedding_venue", # Added
        "escape_room", "bowling_alley", "water_park", "arcade", "circus",
        "karaoke_bar", "theme_park", "tour_operator"
    ],

    "Industrial & Manufacturing": [
        "factory", "warehouse", "metal_fabricator", "printing_service",
        "recycling_center", "chemical_supplier", "engineering_firm",
        "plastics_manufacturer", "packaging_supplier", "logistics", # Added
        "wholesale" # Added
    ],
    # --- New Categories Added ---
    "Transportation & Logistics": [
        "moving_company", # Moved/Copied from Trades
        "storage", # Copied from Trades
        "courier_service", # Added
        "taxi_service", # Added
        "logistics", # Copied from Industrial
        "airport", # Added
        "train_station", # Added
        "bus_station" # Added
        # warehouse could also fit here
    ],
    "Real Estate Services": [
        "real_estate_agency", # Copied from Finance/Pro Services
        "property_management", # Added
        "building_developer", # Added
        "surveyor" # Added (related profession)
    ]
}

# --- Keyword Synonym Mapping ---
# Maps a primary industry keyword (from industry_categories)
# to a list of alternative terms to also search for.

INDUSTRY_SYNONYMS = {
    # Health & Beauty
    "barber_shop": ["barber", "men's grooming"],
    "hair_salon": ["hairdresser", "stylist", "hair studio"],
    "beauty_salon": ["beautician", "skin care clinic", "aesthetics clinic", "beauty parlour"],
    "spa": ["day spa", "health spa", "wellness center"],
    "pharmacy": ["drugstore", "chemist"],
    "dentist": ["dental clinic", "dental surgery", "orthodontist"],
    "doctor": ["gp", "medical clinic", "physician"],
    "physiotherapist": ["physical therapist", "physio"],
    "optician": ["optometrist", "eye doctor", "glasses store"],
    "massage_therapist": ["massage therapy", "masseur", "masseuse"],
    "veterinary_care": ["vet", "animal hospital", "veterinarian"],

    # Hospitality & Food
    "restaurant": ["eatery", "diner", "bistro", "food place"],
    "cafe": ["coffee shop", "coffeeshop", "tea room", "internet cafe"],
    "bar": ["pub", "tavern", "lounge", "inn"],
    "fast_food": ["takeaway", "take out", "fast food restaurant"],
    "night_club": ["club", "disco"],
    "catering_service": ["caterer", "event catering"],
    "lodging": ["accommodation", "inn"], # Generic term for hotel/motel etc.
    "hotel": ["inn"], # Hotel is usually specific enough
    "grocery_store": ["food market", "grocer"],

    # Retail & Shopping
    "clothing_store": ["fashion boutique", "apparel store", "clothes shop"],
    "shoe_store": ["footwear store"],
    "supermarket": ["grocery store"], # Overlap is fine
    "jewelry_store": ["jeweller"],
    "home_goods_store": ["homewares", "home store"],
    "book_store": ["bookshop"],
    "hardware_store": ["diy store"],
    "liquor_store": ["off licence", "wine shop", "bottle shop"],
    "mobile_phone_store": ["phone shop"],
    "second_hand_store": ["charity shop", "thrift store", "consignment shop"],

    # Automotive
    "car_repair": ["mechanic", "garage", "auto repair", "car service", "mot centre"],
    "gas_station": ["petrol station", "fuel station", "filling station"],
    "tire_shop": ["tyre centre", "tyre shop"],

    # Trades & Services
    "plumber": ["plumbing services", "heating engineer"],
    "electrician": ["electrical contractor", "electrical services"],
    "roofing_contractor": ["roofer"],
    "construction_company": ["builder", "building contractor"],
    "gardener": ["gardening services"],
    "landscaper": ["landscaping services"],
    "cleaning_service": ["cleaners", "commercial cleaning", "domestic cleaning"],
    "storage": ["self storage", "storage units"],
    "laundry": ["launderette", "dry cleaners"],

    # Finance & Professional Services
    "accounting": ["accountant", "bookkeeping service"],
    "insurance_agency": ["insurance broker"],
    "lawyer": ["solicitor", "barrister", "law firm", "legal advice"],
    "real_estate_agency": ["estate agent", "realtor"],
    "marketing_agency": ["advertising agency", "digital marketing"],
    "photographer": ["photography studio"],

    # Fitness & Recreation
    "gym": ["health club", "fitness studio"],
    "fitness_center": ["gym", "health club"], # Allow overlap

    # Education & Childcare
    "child_care": ["nursery", "creche", "daycare"],
    "driving_school": ["driving lessons", "driving instructor"],

    # Tech & IT Services
    "it_services": ["it support", "managed it services"],
    "computer_store": ["pc store"],
    "computer_repair": ["pc repair", "laptop repair"],
    "web_design": ["web developer", "website agency"],
    "software_company": ["software house", "software development"],

    # Entertainment & Tourism
    "movie_theater": ["cinema", "picture house"],

    # Industrial & Manufacturing
    "printing_service": ["printers", "print shop"],
    "logistics": ["haulage", "transport company", "freight forwarder"]

    # Add more as needed...
}

# FIX #5: Load any previously failed rows from file on app startup
load_failed_rows_from_file()

# Streamlit Web App
st.title("Business Finder for Notion CRM")
st.write("Find businesses by industry and location, then save them directly to your CRM.")

# Create tabs for different sections
tab1, tab2, tab3 = st.tabs(["Search Businesses", "Failed Jobs", "App Settings"])

# ==============================================================================
# TAB 1: Search Businesses - UPDATED UI and Button Logic
# ==============================================================================
with tab1:
    st.subheader("1. Select Industry")
    col1_industry, col2_industry_multi = st.columns([1, 2])
    with col1_industry:
        selected_category = st.selectbox(
            "Select Industry Category",
            list(industry_categories.keys()),
            key="industry_category_selector"
        )
    with col2_industry_multi:
        if selected_category in industry_categories:
            selected_industries = st.multiselect(
                f"Select Specific Industries in '{selected_category}'",
                industry_categories[selected_category],
                key="industry_multiselect"
            )
        else:
            st.error("Selected category not found in configuration.")
            selected_industries = []

    st.divider()

    st.subheader("2. Select Location")
    location_input_method = st.radio(
        "Location Input Method:",
        ["Select Location from Lists", "Enter Location Manually"],
        key="location_input_mode",
        horizontal=True
    )

    locations_from_list = []
    region_from_list = None
    manual_location_input_str = ""

    if location_input_method == "Select Location from Lists":
        col1_region, col2_location_multi = st.columns([1, 2])
        with col1_region:
            region_from_list = st.selectbox(
                "Select Region",
                list(regions.keys()),
                key="region_selector"
            )
        with col2_location_multi:
            if region_from_list in regions:
                available_locations = regions[region_from_list]
                locations_from_list = st.multiselect(
                    f"Select Locations in '{region_from_list}'",
                    available_locations,
                    key="location_multiselect"
                )
            elif region_from_list:
                st.warning(f"Region '{region_from_list}' not found.")
            else:
                st.info("Select a region to see available locations.")

    elif location_input_method == "Enter Location Manually":
        manual_location_input_str = st.text_input(
            "Enter Location (e.g., 'Temple Bar, Dublin', 'London Eye', 'BT35 8PE')",
            key="manual_location_input",
            placeholder="Type a place, neighborhood, address, or postcode..."
        )

    st.divider()

    st.subheader("3. Configure Search Options")
    grid_cell_radius_km = st.slider(
        "Grid Search Cell Radius (km) - Smaller radius finds more in dense areas but uses more API calls",
        min_value=0.2,
        max_value=5.0,
        value=1.0,
        step=0.1,
        key="grid_radius_slider"
    )
    st.caption(f"Selected grid cell radius: {grid_cell_radius_km * 1000:.0f} meters.")

    assigned_to = st.selectbox(
        "Assign Found Leads to Team Member",
        team_members if 'team_members' in globals() and team_members else ["Default"],
        key="assignee_selector"
    )

    st.divider()
    st.subheader("4. Start Search")

    if st.button("Find Businesses"):

        current_selected_industries = st.session_state.get("industry_multiselect", [])
        input_mode = st.session_state.get("location_input_mode", "Select Location from Lists")
        search_target = None
        region_context = None

        if input_mode == "Select Location from Lists":
            search_target = st.session_state.get("location_multiselect", [])
            region_context = st.session_state.get("region_selector")
            if not search_target:
                st.error("Please select at least one location.")
                st.stop()
            if not region_context:
                st.error("Please select a region.")
                st.stop()
            logger.info(f"Starting search. Mode: List. Locations: {search_target}, Region: {region_context}")
        elif input_mode == "Enter Location Manually":
            search_target = st.session_state.get("manual_location_input", "").strip()
            if not search_target:
                st.error("Please enter a location manually.")
                st.stop()
            logger.info(f"Starting search. Mode: Manual. Location: '{search_target}'")

        grid_radius_value_km = st.session_state.get("grid_radius_slider", 1.0)

        if not GOOGLE_API_KEY:
            st.error("Google API Key not found.")
            st.stop()
        if not current_selected_industries:
            st.error("Please select at least one industry.")
            st.stop()

        st.info(f"Fetching businesses for '{', '.join(current_selected_industries)}'...")
        with st.spinner("Fetching businesses... This may take a while..."):
            try:
                all_businesses = get_businesses(
                    industries=current_selected_industries,
                    search_target=search_target,
                    grid_cell_radius_km=grid_radius_value_km,
                    region=region_context
                )

                if all_businesses is not None:
                    st.success(f"Found {len(all_businesses)} unique businesses.")
                    logger.info(f"get_businesses returned {len(all_businesses)} businesses.")
                    st.session_state.all_businesses = all_businesses
                else:
                    st.error("An error occurred during the business search process.")
                    st.session_state.all_businesses = []

            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
                logger.error(f"Unexpected error calling get_businesses: {e}", exc_info=True)
                st.session_state.all_businesses = []

    # ===============================
    # 5. Show Results + Save Button
    # ===============================
    if "all_businesses" in st.session_state and st.session_state.all_businesses:
        st.divider()
        st.subheader("5. Preview Results")

        df = pd.DataFrame(st.session_state.all_businesses)
        st.dataframe(df, use_container_width=True)

        if st.button("Save Results to Google Sheets"):
            sheet = connect_to_google_sheets()
            if sheet:
                # Check if sheet is empty, add header row
                try:
                    if not sheet.get_all_values():  # No rows in sheet
                        headers = list(df.columns) + ["Assigned To"]
                        sheet.append_row(headers)
                        logger.info("Added header row to empty Google Sheet.")
                except Exception as e:
                    st.error(f"Failed to verify or create headers: {e}")
                    logger.error(f"Header check failed: {e}")
                    st.stop()

                # Progress setup
                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0
                total = len(st.session_state.all_businesses)

                for i, row in enumerate(st.session_state.all_businesses):
                    row_data = list(row.values()) + [assigned_to]
                    business_name = row.get("name", "Unknown")

                    status_text.text(f"Saving {i+1}/{total}: {business_name}")
                    progress_bar.progress((i+1)/total)

                    if safe_append(sheet, row_data, business_name):
                        success_count += 1

                progress_bar.empty()
                status_text.empty()

                if success_count == total:
                    st.success(f"✅ Successfully saved all {success_count} businesses to Google Sheets.")
                else:
                    st.warning(f"⚠️ Saved {success_count}/{total} businesses. Some failed and were added to retry queue.")
            else:
                st.error("❌ Failed to connect to Google Sheets. Check token or spreadsheet name.")

# (The code for the preview table and "Save" button follows this)
# if "all_businesses" in st.session_state and st.session_state.all_businesses:
#    ... (rest of your code for tab1, then tab2, tab3) ...
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