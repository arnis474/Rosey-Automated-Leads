# File: utils/api_utils.py

import requests
import time
import random
import logging
import streamlit as st
import os  # For .env fallback

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
# Load API key from secrets.toml OR .env OR default
GOOGLE_PLACES_API_KEY = (
    st.secrets.get("GOOGLE_API_KEY") or
    os.getenv("GOOGLE_API_KEY") or
    None
)

if not GOOGLE_PLACES_API_KEY:
    st.error("Google API Key not found in Streamlit secrets or environment. Please add it.")
    logging.error("Google Places API Key is missing. Cannot proceed with API requests.")

PLACES_API_ENDPOINT_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_API_ENDPOINT_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"
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

    request_params = params.copy()
    request_params['key'] = GOOGLE_PLACES_API_KEY

    current_backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(endpoint_url, params=request_params, timeout=15)
            response.raise_for_status()

            data = response.json()
            status = data.get('status')

            if status == 'OK':
                logging.info(f"API request successful (Status: OK) for endpoint: {endpoint_url}, params: {params.get('pagetoken', 'Initial Query')}")
                return data
            elif status == 'ZERO_RESULTS':
                logging.info(f"API returned ZERO_RESULTS for endpoint: {endpoint_url}, params: {params.get('pagetoken', 'Initial Query')}")
                return data
            elif status == 'OVER_QUERY_LIMIT':
                logging.warning(f"API Error Status: {status}. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s...")
            elif status == 'INVALID_REQUEST':
                logging.error(f"API returned INVALID_REQUEST. Check parameters for endpoint {endpoint_url}: {params}")
                logging.error(f"Error message: {data.get('error_message', 'No error message provided.')}")
                return None
            elif status in ['REQUEST_DENIED', 'UNKNOWN_ERROR']:
                logging.error(f"API Error Status: {status} for endpoint {endpoint_url}. Params: {params}")
                logging.error(f"Error message: {data.get('error_message', 'No error message provided.')}")
                if status == 'REQUEST_DENIED':
                    return None
            else:
                logging.error(f"Unexpected API Status: {status} for endpoint {endpoint_url}. Params: {params}")

        except requests.exceptions.Timeout as e:
            logging.warning(f"Request timed out. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s... Error: {e}")
        except requests.exceptions.HTTPError as e:
            logging.error(f"HTTP error occurred: {e}. Status Code: {e.response.status_code}. Attempt {attempt + 1}/{MAX_RETRIES}.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during API request. Attempt {attempt + 1}/{MAX_RETRIES}. Retrying in {current_backoff:.2f}s... Error: {e}")

        if attempt < MAX_RETRIES - 1:
            jitter = current_backoff * 0.4 * (random.random() - 0.5)
            sleep_time = max(0.1, current_backoff + jitter)
            logging.info(f"   Sleeping for {sleep_time:.2f} seconds...")
            time.sleep(sleep_time)
            current_backoff = min(current_backoff * 2, MAX_BACKOFF_SECONDS)
        else:
            logging.error(f"API request failed after {MAX_RETRIES} attempts for endpoint {endpoint_url}, params: {params}.")
            return None

    return None