# File: utils/radius_utils.py

import math
import logging
import time

# --- Imports ---
# Import the adapted pagination function
try:
    from .pagination import fetch_places_paginated_generic
except ImportError:
    logging.error("Could not import fetch_places_paginated_generic from utils.pagination.")
    def fetch_places_paginated_generic(*args, **kwargs):
        logging.error("Dummy fetch_places_paginated_generic used. Pagination will fail.")
        return None, set()

# Import the correct API endpoint for text search
try:
    from .api_utils import PLACES_API_ENDPOINT_TEXT_SEARCH
except ImportError:
    logging.warning("Could not import PLACES_API_ENDPOINT_TEXT_SEARCH. Falling back to default URL.")
    PLACES_API_ENDPOINT_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# --- Constants ---
EARTH_RADIUS_METERS = 6371000

# --- Coordinate Calculation ---
def get_point_at_distance(lat_deg, lon_deg, distance_meters, bearing_deg):
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    bearing_rad = math.radians(bearing_deg)

    angular_distance = distance_meters / EARTH_RADIUS_METERS

    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(angular_distance) +
        math.cos(lat_rad) * math.sin(angular_distance) * math.cos(bearing_rad)
    )

    new_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat_rad),
        math.cos(angular_distance) - math.sin(lat_rad) * math.sin(new_lat_rad)
    )

    return math.degrees(new_lat_rad), math.degrees(new_lon_rad)

# --- Grid Generator ---
def generate_grid_points(center_lat, center_lon, city_radius_meters, search_radius_meters):
    if search_radius_meters <= 0:
        logging.error("Search radius must be positive.")
        return []

    grid_points = [(center_lat, center_lon)]
    step_distance = search_radius_meters * 1.5
    if step_distance <= 0:
        logging.warning("Step distance is non-positive. Using center point only.")
        return grid_points

    max_steps = math.ceil(city_radius_meters / step_distance)

    for i in range(1, max_steps + 1):
        distance = i * step_distance
        num_points = max(6, math.ceil((2 * math.pi * distance) / step_distance))
        angle_step = 360.0 / num_points

        for j in range(num_points):
            bearing = j * angle_step
            lat, lon = get_point_at_distance(center_lat, center_lon, distance, bearing)
            grid_points.append((lat, lon))

    unique_points = { (round(lat, 6), round(lon, 6)) for lat, lon in grid_points }
    final_grid = list(unique_points)

    logging.info(f"Generated {len(final_grid)} unique grid points (City Radius: {city_radius_meters}m, Search Radius: {search_radius_meters}m)")
    return final_grid

# --- Grid Search ---
def perform_grid_search(
    industry_keyword,
    grid_points,
    search_radius_meters,
    processed_businesses_set,
    safe_request_func,
    api_key
):
    all_new_grid_results = []

    if not grid_points:
        logging.warning("No grid points provided.")
        return []

    logging.info(f"--- Starting Grid Search over {len(grid_points)} points ---")

    for i, (lat, lon) in enumerate(grid_points):
        point_number = i + 1
        logging.info(f"Grid Point {point_number}/{len(grid_points)}: ({lat:.5f}, {lon:.5f})")

        # Use text search format
        query_string = f"{industry_keyword} near {lat},{lon}"
        params = {
            'query': query_string
        }

        results_for_point, ids_from_point = fetch_places_paginated_generic(
            initial_params=params,
            safe_request_func=safe_request_func,
            api_key=api_key,
            endpoint_url=PLACES_API_ENDPOINT_TEXT_SEARCH
        )

        new_this_point = 0
        if results_for_point:
            for place in results_for_point:
                place_id = place.get("place_id")
                if place_id and place_id not in processed_businesses_set:
                    all_new_grid_results.append(place)
                    processed_businesses_set.add(place_id)
                    new_this_point += 1

            logging.info(f"Grid Point {point_number}: Added {new_this_point} new unique results.")
        elif results_for_point is None:
            logging.error(f"Grid Point {point_number}: API fetch failed completely.")
        else:
            logging.info(f"Grid Point {point_number}: No results found.")

        # Optional delay
        # time.sleep(0.2)

    logging.info(f"--- Grid Search Completed: {len(all_new_grid_results)} total new businesses added ---")
    return all_new_grid_results
