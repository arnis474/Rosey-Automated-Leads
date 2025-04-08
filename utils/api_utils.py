# File: utils/api_utils.py

import requests
import time
import random
import logging
import streamlit as st # Import streamlit to access secrets

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
# Load API key from Streamlit secrets
try:
    GOOGLE_PLACES_API_KEY = st.secrets["google_api_key"]["key"]
except KeyError:
    st.error("Google API Key not found in Streamlit Secrets. Please add it.")
    GOOGLE_PLACES_API_KEY = None # Handle cases where key might be missing
except Exception as e:
    st.error(f"Error loading Google API Key: {e}")
    GOOGLE_PLACES_API_KEY = None


PLACES_API_ENDPOINT_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_API_ENDPOINT_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json" # We might need this later too
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 32

def make_api_request_with_retry(params, endpoint_url=PLACES_API_ENDPOINT_NEARBY):
    """
    Makes a request to the specified Google Places API endpoint
    with exponential backoff retry logic.

    Args:
        params (dict): Dictionary of query parameters for the API request.
        endpoint_url (str): The API endpoint URL to use.

    Returns:
        dict: The JSON response from the API, or None if all retries fail or key is missing.
    """
    if not GOOGLE_PLACES_API_KEY:
         logging.error("Google API Key is not available. Cannot make API request.")
         return None

    # Ensure the key is in params, but don't modify the original dict if passed by reference elsewhere
    request_params = params.copy()
    request_params['key'] = GOOGLE_PLACES_API_KEY

    current_backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(endpoint_url, params=request_params, timeout=15)
            response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)

            data = response.json()
            status = data.get('status')

            # Handle specific statuses
            if status == 'OK':
                logging.info(f"API request successful (Status: OK) for endpoint: {endpoint_url}, params: {params.get('pagetoken', 'Initial Query')}")
                return data
            elif status == 'ZERO_RESULTS':
                logging.info(f"API returned ZERO_RESULTS for endpoint: {endpoint_url}, params: {params.get('pagetoken', 'Initial Query')}")
                return data # Return the empty result set, not an error
            elif status == 'OVER_QUERY_LIMIT':
                 logging.warning(f"API Error Status: {status}. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s...")
                 # Fall through to retry sleep/backoff logic
            elif status == 'INVALID_REQUEST':
                 logging.error(f"API returned INVALID_REQUEST. Check parameters for endpoint {endpoint_url}: {params}")
                 logging.error(f"Error message: {data.get('error_message', 'No error message provided.')}")
                 return None # Cannot recover from this by retrying
            elif status in ['REQUEST_DENIED', 'UNKNOWN_ERROR']: # REQUEST_DENIED usually means API key issue or API not enabled
                 logging.error(f"API Error Status: {status} for endpoint {endpoint_url}. Params: {params}")
                 logging.error(f"Error message: {data.get('error_message', 'No error message provided.')}")
                 # Retry UNKNOWN_ERROR, but not REQUEST_DENIED
                 if status == 'REQUEST_DENIED':
                     return None # Fail immediately
                 # else status is UNKNOWN_ERROR, fall through to retry logic
            else: # Should not happen based on documentation, but capture anything else
                 logging.error(f"Unexpected API Status: {status} for endpoint {endpoint_url}. Params: {params}")
                 # Fall through to retry logic just in case it's transient

        except requests.exceptions.Timeout as e:
            logging.warning(f"Request timed out. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s... Error: {e}")
        except requests.exceptions.HTTPError as e:
             # Specific handling for HTTP errors if needed (e.g. 403 Forbidden might be similar to REQUEST_DENIED)
             logging.error(f"HTTP error occurred: {e}. Status Code: {e.response.status_code}. Attempt {attempt + 1}/{MAX_RETRIES}.")
             # Decide if retry is appropriate based on status code, e.g. don't retry 4xx unless it's 429 (Too Many Requests)
             # For simplicity now, we rely mostly on Google's status codes, but could refine here. Let's retry for now.
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during API request. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s... Error: {e}")

        # --- Retry Delay ---
        if attempt < MAX_RETRIES - 1:
            jitter = current_backoff * 0.4 * (random.random() - 0.5) # +/- 20% jitter
            sleep_time = max(0.1, current_backoff + jitter) # Ensure minimum sleep time
            logging.info(f"   Sleeping for {sleep_time:.2f} seconds...")
            time.sleep(sleep_time)
            current_backoff = min(current_backoff * 2, MAX_BACKOFF_SECONDS)
        else:
             logging.error(f"API request failed after {MAX_RETRIES} attempts for endpoint {endpoint_url}, params: {params}.")
             return None # All retries failed

    return None # Fallback return