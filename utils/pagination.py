# File: utils/pagination.py

import time
import logging
# We will pass the safe_request function as an argument now

# --- Constants ---
MAX_RESULTS_PER_QUERY = 60 # Google Places limit (20 per page, max 3 pages)
RESULTS_PER_PAGE = 20
MAX_PAGES = MAX_RESULTS_PER_QUERY // RESULTS_PER_PAGE
PAGE_TOKEN_DELAY_SECONDS = 2 # Crucial delay required by Google

# Define API endpoints (can be imported or defined here)
PLACES_API_ENDPOINT_TEXTSEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
# PLACES_API_ENDPOINT_NEARBY = "https://maps.googleapis.com/maps/api/place/nearbysearch/json" # Keep if needed elsewhere

def fetch_places_paginated_generic(initial_params, safe_request_func, api_key, endpoint_url=PLACES_API_ENDPOINT_TEXTSEARCH):
    """
    Fetches up to MAX_RESULTS_PER_QUERY places using Google Places API pagination.
    Designed to be generic and use a passed-in request function (like safe_request).

    Args:
        initial_params (dict): Initial query parameters compatible with the endpoint
                            (e.g., {'location':'lat,lon', 'radius':radius, 'query':keyword} for TextSearch).
                            MUST NOT include 'key'.
        safe_request_func (function): The function to use for making API requests (e.g., safe_request from app.py).
                                    It should accept a URL string and return parsed JSON or None.
        api_key (str): The Google API Key.
        endpoint_url (str): The Google Places API endpoint URL to use.

    Returns:
        list: A list of raw place result dictionaries, or None if the initial request fails.
        set: A set of unique place_ids collected during this specific paginated search run.
    """
    if not api_key:
        logging.error("API Key missing, cannot fetch places.")
        return None, set()

    all_results_this_run = []
    seen_place_ids_this_run = set() # Tracks IDs found *within this specific paginated query*
    current_page = 0
    next_page_token = None

    # --- Initial Request (Page 1) ---
    page_num = current_page + 1
    logging.info(f"Paginated Fetch: Requesting Page {page_num}/{MAX_PAGES} from {endpoint_url}")

    # Construct URL for the first request
    # Add key to a copy of params
    params_with_key = initial_params.copy()
    params_with_key['key'] = api_key
    # Create URL with query parameters properly encoded (requests usually handles this, but being explicit)
    query_string = '&'.join([f"{k}={v}" for k, v in params_with_key.items()])
    current_url = f"{endpoint_url}?{query_string}"

    response_data = safe_request_func(current_url) # Use the passed-in request function

    if response_data is None:
         logging.error(f"Paginated Fetch: Initial API request failed for params: {initial_params}. Aborting this fetch.")
         return None, set() # Indicate failure

    results_this_page = response_data.get('results', [])
    status = response_data.get('status')

    if status == 'OK':
        for place in results_this_page:
             place_id = place.get('place_id')
             if place_id and place_id not in seen_place_ids_this_run:
                 all_results_this_run.append(place)
                 seen_place_ids_this_run.add(place_id)
        next_page_token = response_data.get('next_page_token')
        logging.info(f"Paginated Fetch: Page {page_num} - Found {len(results_this_page)} results. New unique this run: {len(seen_place_ids_this_run)}. Next Token: {'Yes' if next_page_token else 'No'}")
    elif status == 'ZERO_RESULTS':
         logging.info(f"Paginated Fetch: Initial query returned ZERO_RESULTS for params: {initial_params}")
         return [], set() # No results found
    else:
         # Handle other non-OK statuses from the first request
         logging.error(f"Paginated Fetch: Initial request failed. Status: {status}. Error: {response_data.get('error_message')}. Params: {initial_params}")
         return None, set() # Indicate failure


    # --- Loop for Subsequent Pages (Pages 2, 3) ---
    current_page += 1
    while next_page_token and current_page < MAX_PAGES:
        page_num = current_page + 1
        logging.info(f"Paginated Fetch: Found next_page_token. Waiting {PAGE_TOKEN_DELAY_SECONDS}s before fetching page {page_num}.")
        time.sleep(PAGE_TOKEN_DELAY_SECONDS)

        # Construct URL for the next page (only token and key needed)
        next_page_url = f"{endpoint_url}?pagetoken={next_page_token}&key={api_key}"

        logging.info(f"Paginated Fetch: Requesting Page {page_num}/{MAX_PAGES}...")
        response_data = safe_request_func(next_page_url) # Use the passed-in request function

        if response_data is None:
             logging.warning(f"Paginated Fetch: API request failed for page {page_num} (token: {next_page_token}). Proceeding with results gathered so far.")
             break # Stop pagination if a subsequent page fails

        results_this_page = response_data.get('results', [])
        status = response_data.get('status')
        newly_added_count = 0

        if status == 'OK':
            for place in results_this_page:
                 place_id = place.get('place_id')
                 if place_id and place_id not in seen_place_ids_this_run:
                     all_results_this_run.append(place)
                     seen_place_ids_this_run.add(place_id)
                     newly_added_count +=1
            next_page_token = response_data.get('next_page_token') # Get token for the *next* page
            logging.info(f"Paginated Fetch: Page {page_num} - Found {len(results_this_page)} results. New unique this run: {newly_added_count}. Next Token: {'Yes' if next_page_token else 'No'}")

            # Optional safety break
            if not results_this_page and next_page_token:
                 logging.warning("Paginated Fetch: Received next_page_token but no results, stopping.")
                 break
        elif status == 'INVALID_REQUEST':
             # This can happen if the token expires or is used too quickly
             logging.warning(f"Paginated Fetch: Received INVALID_REQUEST for page {page_num}. Token might be invalid. Stopping pagination.")
             next_page_token = None # Ensure loop terminates
             break
        else:
             # Log other statuses but try to continue if possible? Or break? Let's break.
             logging.warning(f"Paginated Fetch: Received status '{status}' for page {page_num}. Stopping pagination.")
             next_page_token = None
             break

        current_page += 1
        # Small delay before potentially starting next loop iteration (optional)
        # time.sleep(0.1)

    logging.info(f"Paginated Fetch: Finished. Total unique results in this run: {len(all_results_this_run)}")
    return all_results_this_run, seen_place_ids_this_run