import streamlit as st
import requests
import json
import os
import re
import gspread
import time
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

# Load API keys from .env file
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SPREADSHEET_NAME = "Leads"

def safe_append(sheet, row_data, retries=3):
    """Attempts to append a row with retries in case of an API error."""
    for attempt in range(retries):
        try:
            sheet.append_row(row_data)
            return True
        except gspread.exceptions.APIError as e:
            print(f"API Error: {e}. Retrying ({attempt+1}/{retries})...")
            time.sleep(2)  # Wait before retrying
    print("❌ Failed to append row after multiple attempts.")
    return False

# Fetch businesses from Google Places API
def get_businesses(industries, locations):
    businesses = []
    
    for location in locations:
        for industry in industries:
            url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={industry}+in+{location}+Northern+Ireland&key={GOOGLE_API_KEY}"
            response = requests.get(url)
            data = response.json()

            # Debugging: Print API response
            print("\n🔍 DEBUG: API Request URL:", url)
            print("🔍 DEBUG: API Response:", json.dumps(data, indent=2))

            if "results" in data and len(data["results"]) > 0:
                for place in data["results"]:
                    details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place['place_id']}&key={GOOGLE_API_KEY}"
                    details_response = requests.get(details_url).json()
                    details = details_response.get("result", {})

                    website = details.get("website", "N/A")
                    facebook, instagram, twitter, linkedin, tiktok = extract_social_media_links(website)

                    businesses.append({
                        "name": place.get("name", "N/A"),
                        "address": place.get("formatted_address", "N/A"),
                        "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('place_id', '')}",
                        "business_type": industry,
                        "rating": place.get("rating", "N/A"),
                        "phone_number": details.get("formatted_phone_number", "N/A"),
                        "website": website,
                        "facebook": facebook,
                        "instagram": instagram,
                        "twitter": twitter,
                        "linkedin": linkedin,
                        "tiktok": tiktok,
                        "opening_hours": ", ".join(details.get("opening_hours", {}).get("weekday_text", []))
                    })
            else:
                print(f"⚠️ WARNING: No businesses found for '{industry}' in '{location}'.")

    return businesses

# Team Members
team_members = ["Allan", "Arnis", "Matt", "Stan", "James", "Kyle", "Kelvin"]

# Full list of cities, towns, and villages in Northern Ireland
northern_ireland_cities = [
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
]

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

# Authenticate and connect to Google Sheets using OAuth
def connect_to_google_sheets():
    creds = Credentials.from_authorized_user_file("token.json", ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).sheet1

# Extract social media links
def extract_social_media_links(website_url):
    if not website_url or website_url == "N/A":
        return "", "", "", "", ""

    facebook = re.findall(r"https?://(www\.)?facebook\.com/[a-zA-Z0-9._/-]+/?", website_url)
    instagram = re.findall(r"https?://(www\.)?instagram\.com/[a-zA-Z0-9._/-]+/?", website_url)
    twitter = re.findall(r"https?://(www\.)?twitter\.com/[a-zA-Z0-9._/-]+/?", website_url)
    linkedin = re.findall(r"https?://(www\.)?linkedin\.com/in/[a-zA-Z0-9._/-]+/?", website_url)
    tiktok = re.findall(r"https?://(www\.)?tiktok\.com/@[a-zA-Z0-9._/-]+/?", website_url)

    return facebook[0] if facebook else "N/A", instagram[0] if instagram else "N/A", twitter[0] if twitter else "N/A", linkedin[0] if linkedin else "N/A", tiktok[0] if tiktok else "N/A"

# Streamlit Web App
st.title("Business Finder for Notion CRM")

selected_category = st.selectbox("Select Industry Category", list(industry_categories.keys()))
selected_industries = st.multiselect("Select Industries", industry_categories[selected_category])
selected_locations = st.multiselect("Select Locations", northern_ireland_cities)
assigned_to = st.selectbox("Assign to Team Member", team_members)

if st.button("Find Businesses"):
    with st.spinner("Fetching businesses..."):
        all_businesses = get_businesses(selected_industries, selected_locations)

        if all_businesses:
            sheet = connect_to_google_sheets()

            # ✅ Loop through businesses with error handling and delay
            for business in all_businesses:
                row_data = list(business.values()) + [assigned_to]
                
                # Use safe_append function to prevent API errors
                success = safe_append(sheet, row_data)

                if success:
                    st.success(f"✅ Added {business['name']} ({business['address']}) to Google Sheets")
                else:
                    st.error(f"❌ Failed to add {business['name']} ({business['address']}). Check logs.")

                time.sleep(1)  # ✅ Prevent API rate limit issues
        else:
            st.error("No businesses found. Try another location or industry.")