import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from streamlit_geolocation import streamlit_geolocation
from folium.plugins import MarkerCluster

# --- Configurazione e Connessione al Database usando st.secrets ---
try:
    if not firebase_admin._apps:
        # st.secrets si comporta come un dizionario
        firebase_creds_dict = st.secrets["firebase_credentials"]
        cred = credentials.Certificate(firebase_creds_dict)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error(f"⚠️ Errore di connessione a Firebase! Assicurati di aver impostato i Segreti su Streamlit Cloud. Dettagli tecnici: {e}")
    st.stop()

st.set_page_config(layout="wide")
st.title("⛽️ App Prezzi Carburante")

# --- Funzioni di Autenticazione (usano st.secrets) ---
def registra_utente(email, password):
    api_key = st.secrets["firebase_web_api_key"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        user_data = response.json()
        crea_profilo_utente(user_data['localId'], email)
        return user_data
    except requests.exceptions.HTTPError as err:
        return {"error": err.response.json().get("error", {})}

def accedi_utente(email, password):
    api_key = st.secrets["firebase_web_api_key"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        return {"error": err.response.json().get("error", {})}

def invia_email_verifica(id_token):
    api_key = st.secrets["firebase_web_api_key"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
    payload = {"requestType": "VERIFY_EMAIL", "idToken": id_token}
    try:
        requests.post(url, json=payload)
    except requests.exceptions.HTTPError:
        pass

def elimina_utente(id_token):
    api_key = st.secrets["firebase_web_api_key"]
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:delete?key={api_key}"
    payload = {"idToken": id_token}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        return {"success": True}
    except requests.exceptions.HTTPError as err:
        error_message = err.response.json().get("error", {}).get("message", "ERRORE_SCONOSCIUTO")
        return {"error": error_message}

def crea_profilo_utente(uid, email):
    db.collection("utenti").document(uid).set({
        "email": email, "data_registrazione": firestore.SERVER_TIMESTAMP, "privacy_accepted": False
    })

def get_profilo_utente(uid):
    doc_ref = db.collection("utenti").document(uid)
    doc = doc_ref.get()
    return doc.to_dict() if doc.exists else None

def accetta_privacy(uid):
    db.collection("utenti").document(uid).update({"privacy_accepted": True})

# --- Funzioni di Logica ---
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
        response = requests.get(url); response.raise_for_status()
        risultati = response.json().get("results", [])
        schedario = [{"id": luogo.get("place_id"), "nome": luogo.get("name", "N/D"), "indirizzo": luogo.get("vicinity", "N/D"), "latitudine": str(luogo["geometry"]["location"]["lat"]), "longitudine": str(luogo["geometry"]["location"]["lng"])} for luogo in risultati]
        return schedario
    except Exception as e:
        st.error(f"Errore API Google: {e}"); return []

def leggi_prezzi_da_firebase(lista_distributori):
    prezzi_trovati = {}
    ids = [d['id'] for d in lista_distributori if d.get('id')]
    if not ids: return prezzi_trovati
    docs = db.collection("prezzi_segnalati").where("id", "in", ids).stream()
    for doc in docs:
        prezzi_trovati[doc.id] = doc.to_dict()
    return prezzi_trovati

def salva_prezzo(id_distributore, nome_distributore, tipo_carburante, nuovo_prezzo, user_id):
    try:
        doc_ref = db.collection("prezzi_segnalati").document(id_distributore)
        nuovo_prezzo_data = {
            "valore": nuovo_prezzo, "conferme": 1,
            "segnalato_da": [user_id], "data_inserimento": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set({"id": id_distributore, "nome_distributore": nome_distributore, "prezzi": { tipo_carburante: nuovo_prezzo_data }, "ultimo_aggiornamento": firestore.SERVER_TIMESTAMP}, merge=True)
        st.success(f"Grazie! Prezzo per '{nome_distributore}' aggiornato."); st.cache_data.clear()
    except Exception as e: st.error(f"Errore durante il salvataggio: {e}")

def conferma_prezzo(id_distributore, tipo_carburante, user_id):
    try:
        doc_ref = db.collection("prezzi_segnalati").document(id_distributore)
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            snapshot = doc_ref.get(transaction=transaction)
            dati = snapshot.to_dict()
            if user_id not in dati["prezzi"][tipo_carburante]["segnalato_da"]:
                transaction.update(doc_ref, {f"prezzi.{tipo_carburante}.conferme": firestore.Increment(1), f"prezzi.{tipo_carburante}.segnalato_da": firestore.ArrayUnion([user_id]), "ultimo_aggiornamento": firestore.SERVER_TIMESTAMP})
                return True
            else:
                return False
        transaction = db.transaction()
        result = update_in_transaction(transaction, doc_ref)
        if result: st.success("Grazie per la tua conferma!")
        else: st.warning("Hai già confermato questo prezzo.")
        st.cache_data.clear()
    except Exception as e: st.error(f"Errore durante la conferma: {e}")

# --- Funzioni per la Mappa ---
def crea_mappa_base(centro, zoom):
    return folium.Map(location=centro, zoom_start=zoom, tiles="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}", attr="Google")

def aggiungi_distributori_sulla_mappa(mappa_da_popolare, lista_distributori, prezzi_db, user_location=None):
    marker_cluster = MarkerCluster().add_to(mappa_da_popolare)
    for distributore in lista_distributori:
        lat, lon = float(distributore["latitudine"]), float(distributore["longitudine"])
        info_prezzi_db = prezzi_db.get(distributore.get('id'), {})
        testo_prezzi = ""
        if 'prezzi' in info_prezzi_db:
            for carburante, info_carburante in info_prezzi_db['prezzi'].items():
                prezzo_val = info_carburante.get('valore', 'N/D'); conferme_val = info_carburante.get('conferme', 0)
                testo_prezzi += f"<br><b>{carburante}: {prezzo_val} €</b> ({conferme_val} conferme)"
        popup_html = f"<strong>{distributore['nome']}</strong><br>{distributore['indirizzo']}{testo_prezzi}"
        if user_location:
            link_navigatore = f"http://googleusercontent.com/maps/google.com/4{user_location['latitude']},{user_location['longitude']}&destination={lat},{lon}"
            popup_html += f"<br><br><a href='{link_navigatore}' target='_blank'>➡️ Avvia Navigatore</a>"
        colore_icona = "green" if testo_prezzi else "blue"
        icona = folium.Icon(color=colore_icona, icon="gas-pump", prefix="fa")
        folium.Marker(location=[lat, lon], popup=popup_html, icon=icona).add_to(marker_cluster)

# --- INIZIO APP ---
if 'user_info' not in st.session_state: st.session_state.user_info = None
if 'distributori_trovati' not in st.session_state: st.session_state.distributori_trovati = []
if 'user_location' not in st.session_state: st.session_state.user_location = None

# --- Sezione Sidebar ---
st.sidebar.header("👤 Area Utente")
if not st.session_state.user_info:
    # (codice login/registrazione invariato)
    pass
else:
    # (codice utente loggato invariato)
    pass

# --- Logica di Visualizzazione ---
privacy_accettata = False
if st.session_state.user_info:
    profilo_utente = get_profilo_utente(st.session_state.user_info['localId'])
    if profilo_utente and profilo_utente.get("privacy_accepted", False):
        privacy_accettata = True
    else:
        # Usa una normale area di testo invece di st.modal
        st.subheader("Informativa sulla Privacy")
        st.info("Benvenuto! Per usare le funzioni di contribuzione, devi accettare la nostra informativa.")
        st.write("Raccoglieremo la tua email per l'account e tracceremo i prezzi che segnali per garantire la qualità del servizio.")
        accettato = st.checkbox("Dichiaro di aver letto e accettato l'informativa sulla privacy.")
        if st.button("Continua", disabled=not accettato):
            accetta_privacy(st.session_state.user_info['localId']); st.rerun()
else:
    privacy_accettata = True

if privacy_accettata:
    # (Codice della vista principale dell'app, con ricerca, risultati e modulo segnalazione)
    pass