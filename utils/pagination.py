# File: utils/pagination.py

import time
import logging
# Use relative import if api_utils is in the same directory
from .api_utils import make_api_request_with_retry, PLACES_API_ENDPOINT_NEARBY

# --- Constants ---
MAX_RESULTS_PER_QUERY = 60
RESULTS_PER_PAGE = 20
MAX_PAGES = MAX_RESULTS_PER_QUERY // RESULTS_PER_PAGE
PAGE_TOKEN_DELAY_SECONDS = 2 # Google recommends a short delay

def fetch_places_paginated(initial_params):
    """
    Fetches up to MAX_RESULTS_PER_QUERY places using Google Places API pagination (Nearby Search).

    Args:
        initial_params (dict): Initial query parameters (e.g., location, radius, keyword/type).
                               'key' will be added automatically by make_api_request_with_retry.

    Returns:
        list: A list of raw place result dictionaries, or None if the initial request fails.
        set: A set of unique place_ids collected during this paginated search.
    """
    all_results = []
    seen_place_ids_this_query = set() # Tracks IDs found *within this specific paginated query*
    current_page = 0
    
    # Use the initial params for the first request
    current_params = initial_params.copy() 

    while current_page < MAX_PAGES:
        page_num = current_page + 1
        logging.info(f"Fetching page {page_num}/{MAX_PAGES}...")
        
        # Use the robust request function
        response_data = make_api_request_with_retry(current_params, endpoint_url=PLACES_API_ENDPOINT_NEARBY)

        if response_data is None:
             # Error already logged by make_api_request_with_retry
             logging.error(f"API request failed for params: {current_params}. Stopping pagination for this query.")
             # Return whatever was collected before the failure
             return all_results, seen_place_ids_this_query 

        results_this_page = response_data.get('results', [])
        status = response_data.get('status')

        if status == 'ZERO_RESULTS' and current_page == 0:
             logging.info("Initial query returned ZERO_RESULTS. No places found.")
             return [], set() # Return empty list and set immediately
        elif status == 'ZERO_RESULTS':
             # This might happen if subsequent pages somehow return zero, though unlikely
             logging.info("Subsequent page returned ZERO_RESULTS.")
             # Continue to check for next_page_token just in case, but don't process results

        # Process results from the current page if status is OK
        new_results_count_this_page = 0
        if status == 'OK':
             for place in results_this_page:
                 place_id = place.get('place_id')
                 if place_id:
                     # Deduplication *within* this paginated search run
                     if place_id not in seen_place_ids_this_query:
                         all_results.append(place)
                         seen_place_ids_this_query.add(place_id)
                         new_results_count_this_page += 1
                 else:
                     logging.warning(f"Found a place result without place_id: {place.get('name', 'N/A')}")
             
             logging.info(f"Page {page_num}: Found {len(results_this_page)} results, added {new_results_count_this_page} new unique results for this query.")
        elif status != 'ZERO_RESULTS': # Log other non-OK statuses if they somehow get here
             logging.warning(f"Received status '{status}' on page {page_num}. Results may be incomplete.")


        # Check if we have enough results or if there's a next page
        if len(all_results) >= MAX_RESULTS_PER_QUERY:
            logging.info(f"Reached or exceeded max results ({MAX_RESULTS_PER_QUERY}). Stopping pagination.")
            break 

        next_page_token = response_data.get('next_page_token')

        if next_page_token:
            current_page += 1
            # Only continue if we haven't reached the page limit
            if current_page < MAX_PAGES: 
                logging.info(f"Found next_page_token. Waiting {PAGE_TOKEN_DELAY_SECONDS}s before fetching page {current_page + 1}.")
                time.sleep(PAGE_TOKEN_DELAY_SECONDS) 
                # Prepare params for the next page request - ONLY token and key are needed/allowed
                current_params = {'pagetoken': next_page_token} 
            else:
                 logging.info(f"Reached max pages limit ({MAX_PAGES}). No more pages will be fetched even though a token exists.")
                 break
        else:
            logging.info("No next_page_token found. End of results for this query.")
            break 

    logging.info(f"Pagination complete for initial params {initial_params}. Total unique results found in this run: {len(all_results)}")
    # Return all unique results found in this specific multi-page query
    # and the set of their IDs for cross-query deduplication later.
    return all_results, seen_place_ids_this_query