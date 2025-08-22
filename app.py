import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
from folium.plugins import MarkerCluster

st.set_page_config(layout="wide")
st.title("⛽️ App Prezzi Carburante (Test Google Maps)")
st.info("Questa è una versione di test per verificare la connessione alle API di Google.")

# --- Funzioni di Logica (solo Google Maps) ---
@st.cache_data
def trova_distributori_google(citta):
    try:
        api_key = st.secrets["google_api_key"]
        query = f"distributori di benzina a {citta}"
        url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={query}&key={api_key}&language=it"
        response = requests.get(url)
        response.raise_for_status()
        risultati = response.json().get("results", [])
        schedario = [{"nome": luogo.get("name", "N/D"), "indirizzo": luogo.get("vicinity", "N/D"), "latitudine": str(luogo["geometry"]["location"]["lat"]), "longitudine": str(luogo["geometry"]["location"]["lng"])} for luogo in risultati]
        return schedario
    except Exception as e:
        st.error(f"Errore durante la chiamata a Google API. Hai impostato correttamente la 'google_api_key' nei Segreti? Dettagli: {e}")
        return []

# --- Funzioni per la Mappa ---
def crea_mappa_base(centro, zoom):
    return folium.Map(location=centro, zoom_start=zoom, tiles="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}", attr="Google")

def aggiungi_distributori_sulla_mappa(mappa_da_popolare, lista_distributori):
    marker_cluster = MarkerCluster().add_to(mappa_da_popolare)
    for distributore in lista_distributori:
        lat, lon = float(distributore["latitudine"]), float(distributore["longitudine"])
        popup_html = f"<strong>{distributore['nome']}</strong><br>{distributore['indirizzo']}"
        icona = folium.Icon(color="blue", icon="gas-pump", prefix="fa")
        folium.Marker(location=[lat, lon], popup=popup_html, icon=icona).add_to(marker_cluster)

# --- INIZIO APP ---
st.header("🌍 Cerca per Città")
citta_cercata = st.text_input("Scrivi il nome di un comune e premi Invio:")

if citta_cercata:
    distributori_trovati = trova_distributori_google(citta_cercata)
    if distributori_trovati:
        st.success(f"Trovati {len(distributori_trovati)} distributori a {citta_cercata.capitalize()} tramite Google.")
        mappa_citta = crea_mappa_base(centro=[float(distributori_trovati[0]['latitudine']), float(distributori_trovati[0]['longitudine'])], zoom=12)
        aggiungi_distributori_sulla_mappa(mappa_citta, distributori_trovati)
        st_folium(mappa_citta, width="100%", height=500)
    else:
        st.warning("Nessun distributore trovato per la città cercata.")