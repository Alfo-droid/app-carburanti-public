import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from streamlit_geolocation import streamlit_geolocation
from folium.plugins import MarkerCluster
import json

# --- Configurazione e Connessione al Database usando st.secrets ---
try:
    if not firebase_admin._apps:
        firebase_creds_dict = st.secrets["firebase_credentials"]
        cred = credentials.Certificate(firebase_creds_dict)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error(f"⚠️ Errore di connessione a Firebase! Dettagli: {e}")
    st.stop()

st.set_page_config(layout="wide")
st.title("⛽️ App Prezzi Carburante")

# --- Funzioni di Autenticazione ---
# (Tutte le funzioni di autenticazione e profilo utente rimangono qui, invariate)

# --- Funzioni di Logica (CON MODIFICA PER DEBUG) ---
@st.cache_data
def trova_distributori_google(citta=None, coordinate=None):
    api_key = st.secrets["google_api_key"]
    if coordinate:
        lat, lon = coordinate['latitude'], coordinate['longitude']
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?location={lat},{lon}&radius=5000&type=gas_station&key={api_key}&language=it"
    elif citta:
        query = f"distributori di benzina a {citta}"
        url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={query}&key={api_key}&language=it"
    else: return []
    
    try:
        response = requests.get(url)
        
        # --- RIGA DI DEBUG: Mostra la risposta grezza di Google ---
        st.write("--- Risposta da Google API (DEBUG): ---")
        st.json(response.json())
        # --- FINE RIGA DI DEBUG ---

        response.raise_for_status()
        risultati = response.json().get("results", [])
        schedario = [{"id": luogo.get("place_id"), "nome": luogo.get("name", "N/D"), "indirizzo": luogo.get("vicinity", "N/D"), "latitudine": str(luogo["geometry"]["location"]["lat"]), "longitudine": str(luogo["geometry"]["location"]["lng"])} for luogo in risultati]
        return schedario
    except Exception as e:
        st.error(f"Errore API Google: {e}"); return []

# (Tutto il resto del codice, funzioni e logica dell'app, rimane identico a prima)
# ...