# File: utils/api_utils.py

import requests
import time
import random
import logging
import streamlit as st
import os
from dotenv import load_dotenv

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load from .env if available ---
load_dotenv()

# --- Load API key from Streamlit secrets OR fallback to environment variable ---
GOOGLE_PLACES_API_KEY = (
    st.secrets.get("GOOGLE_API_KEY", None) or
    os.getenv("GOOGLE_API_KEY")
)

# --- Handle missing API key ---
if not GOOGLE_PLACES_API_KEY:
    st.error("❌ Google API Key not found. Please add it to `.env` or `secrets.toml`.")
    logging.error("Google Places API Key is missing. Cannot proceed with API requests.")

# --- Google Places API Constants ---
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
        logging.error("❌ Google API Key not available. Aborting request.")
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
                logging.info(f"✅ Success on attempt {attempt+1}")
                return data
            elif status == 'ZERO_RESULTS':
                logging.info(f"ℹ️ Zero results for params: {params}")
                return data
            elif status == 'OVER_QUERY_LIMIT':
                logging.warning(f"⚠️ Over query limit. Backing off ({current_backoff}s)...")
            elif status == 'INVALID_REQUEST':
                logging.error(f"❌ Invalid request. Params: {params}")
                logging.error(f"Message: {data.get('error_message')}")
                return None
            elif status == 'REQUEST_DENIED':
                logging.error(f"❌ Request denied. API key issue or API not enabled. Params: {params}")
                logging.error(f"Message: {data.get('error_message')}")
                return None
            elif status == 'UNKNOWN_ERROR':
                logging.warning(f"Unknown error. Will retry...")
            else:
                logging.error(f"Unexpected status: {status}. Full response: {data}")

        except requests.exceptions.Timeout:
            logging.warning(f"⏱️ Timeout on attempt {attempt+1}. Retrying in {current_backoff}s...")
        except requests.exceptions.RequestException as e:
            logging.error(f"📡 Network error: {e}. Retrying in {current_backoff}s...")

        # Backoff before retrying
        if attempt < MAX_RETRIES - 1:
            jitter = current_backoff * 0.4 * (random.random() - 0.5)
            sleep_time = max(0.1, current_backoff + jitter)
            time.sleep(sleep_time)
            current_backoff = min(current_backoff * 2, MAX_BACKOFF_SECONDS)
        else:
            logging.error("❌ Max retries reached. Request failed.")
            return None

    return None
