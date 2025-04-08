# File: utils/radius_utils.py

import math
import logging
import time

# Import the adapted pagination function (use relative import)
# Make sure pagination.py contains fetch_places_paginated_generic
try:
    from .pagination import fetch_places_paginated_generic
except ImportError:
    logging.error("Could not import fetch_places_paginated_generic from utils.pagination. Make sure pagination.py is correctly set up.")
    # Define a dummy function to avoid crashing, but log error
    def fetch_places_paginated_generic(*args, **kwargs):
        logging.error("Dummy fetch_places_paginated_generic called - pagination will not work correctly.")
        return None, set()


# --- Constants ---
EARTH_RADIUS_METERS = 6371000 # Approximate Earth radius

# --- Helper Function for Coordinate Calculation ---
def get_point_at_distance(lat_deg, lon_deg, distance_meters, bearing_deg):
    """
    Calculates the lat/lon of a point a certain distance and bearing from another point.
    Uses Haversine formula components.
    """
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    bearing_rad = math.radians(bearing_deg)

    angular_distance = distance_meters / EARTH_RADIUS_METERS

    new_lat_rad = math.asin(math.sin(lat_rad) * math.cos(angular_distance) +
                            math.cos(lat_rad) * math.sin(angular_distance) * math.cos(bearing_rad))

    new_lon_rad = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat_rad),
                                      math.cos(angular_distance) - math.sin(lat_rad) * math.sin(new_lat_rad))

    return math.degrees(new_lat_rad), math.degrees(new_lon_rad)

# --- Grid Generation Function ---
def generate_grid_points(center_lat, center_lon, city_radius_meters, search_radius_meters):
    """
    Generates a grid of lat/lon points covering a circular area for grid search.
    Points are spaced such that circles of search_radius_meters overlap significantly.
    """
    if search_radius_meters <= 0:
         logging.error("Search radius must be positive.")
         return []

    grid_points = [(center_lat, center_lon)] # Start with the exact center

    # Calculate step distance for placing grid centers.
    step_distance = search_radius_meters * 1.5 # Factor for overlap

    if step_distance <= 0:
         logging.warning("Calculated step distance is zero or negative. Only center point will be used.")
         return grid_points

    max_steps = math.ceil(city_radius_meters / step_distance)

    # Generate points in concentric rings
    for i in range(1, max_steps + 1):
        distance_from_center = i * step_distance
        num_points_on_ring = max(6, math.ceil((2 * math.pi * distance_from_center) / step_distance))
        angle_step = 360.0 / num_points_on_ring

        for j in range(num_points_on_ring):
             bearing = j * angle_step
             lat, lon = get_point_at_distance(center_lat, center_lon, distance_from_center, bearing)
             grid_points.append((lat, lon))

    unique_points_set = { (round(lat, 6), round(lon, 6)) for lat, lon in grid_points }
    final_grid = list(unique_points_set)

    logging.info(f"Generated {len(final_grid)} unique grid points for radius search (City Radius: {city_radius_meters}m, Search Radius: {search_radius_meters}m).")
    return final_grid

# --- Grid Search Execution Function ---
def perform_grid_search(
    industry_keyword,
    grid_points,
    search_radius_meters,
    processed_businesses_set, # Pass the master set of processed IDs
    safe_request_func,        # Pass the request function
    api_key                   # Pass the API key
):
    """
    Performs a paginated search for each point in the grid using Text Search.
    MODIFIES processed_businesses_set by adding new IDs found.

    Returns:
        list: A list of NEW, UNIQUE business dictionaries found ONLY during the grid search.
    """
    all_new_grid_results = []

    if not grid_points:
        logging.warning("perform_grid_search called with no grid points.")
        return []

    logging.info(f"--- Starting Grid Search Execution across {len(grid_points)} points ---")

    total_points = len(grid_points)
    for i, (lat, lon) in enumerate(grid_points):
        point_num = i + 1
        logging.info(f"--- Grid Point {point_num}/{total_points}: ({lat:.5f}, {lon:.5f}) ---")

        # Perform Paginated Text Search for this Grid Point
        params = {
            'location': f"{lat},{lon}",
            'radius': str(search_radius_meters),
            'query': industry_keyword
        }

        # Call the adapted pagination function from pagination.py
        results_for_point, ids_from_point = fetch_places_paginated_generic(
            initial_params=params,
            safe_request_func=safe_request_func,
            api_key=api_key
            # endpoint_url defaults to Text Search in the function definition
        )

        new_unique_count_this_point = 0
        if results_for_point:
            logging.info(f"  Grid Point {point_num}: Found {len(results_for_point)} potential results from pagination.")
            for place in results_for_point:
                place_id = place.get('place_id')
                if place_id and place_id not in processed_businesses_set:
                    all_new_grid_results.append(place)
                    processed_businesses_set.add(place_id) # Modify the master set
                    new_unique_count_this_point += 1
            logging.info(f"  Grid Point {point_num}: Added {new_unique_count_this_point} new unique leads to session.")
        elif results_for_point is None:
             logging.error(f"  Grid Point {point_num}: Paginated fetch failed completely.")
        else: # Results list is empty ([])
             logging.info(f"  Grid Point {point_num}: Paginated fetch returned ZERO_RESULTS.")

        # Optional small delay
        # time.sleep(0.2)

    logging.info(f"--- Grid Search Execution Finished. Found {len(all_new_grid_results)} total new unique leads from grid. ---")
    return all_new_grid_results