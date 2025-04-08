import requests
import logging

def get_coordinates(location, api_key):
    """
    Uses the Google Geocoding API to convert a location string into latitude and longitude.

    Args:
        location (str): The address, place, or postcode to geocode.
        api_key (str): Your Google API key.

    Returns:
        tuple: (latitude, longitude) or (None, None) if it fails.
    """
    try:
        endpoint = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": location, "key": api_key}
        response = requests.get(endpoint, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "OK":
            location_data = data["results"][0]["geometry"]["location"]
            return location_data["lat"], location_data["lng"]
        else:
            logging.error(f"Geocoding failed for '{location}'. Status: {data.get('status')}")
            return None, None
    except Exception as e:
        logging.error(f"Exception during geocoding: {e}")
        return None, None
