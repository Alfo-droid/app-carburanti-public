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
    # Trasforma i segreti TOML in un dizionario per Firebase
    firebase_creds_dict = {
        "type": st.secrets["firebase_credentials"]["type"],
        "project_id": st.secrets["firebase_credentials"]["project_id"],
        "private_key_id": st.secrets["firebase_credentials"]["private_key_id"],
        "private_key": st.secrets["firebase_credentials"]["private_key"].replace('\\n', '\n'),
        "client_email": st.secrets["firebase_credentials"]["client_email"],
        "client_id": st.secrets["firebase_credentials"]["client_id"],
        "auth_uri": st.secrets["firebase_credentials"]["auth_uri"],
        "token_uri": st.secrets["firebase_credentials"]["token_uri"],
        "auth_provider_x509_cert_url": st.secrets["firebase_credentials"]["auth_provider_x509_cert_url"],
        "client_x509_cert_url": st.secrets["firebase_credentials"]["client_x509_cert_url"],
    }
    cred = credentials.Certificate(firebase_creds_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except ValueError:
    db = firestore.client()

st.set_page_config(layout="wide")
st.title("⛽️ App Prezzi Carburante")

# --- Funzioni di Autenticazione (usano st.secrets) ---
def registra_utente(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={st.secrets.FIREBASE_WEB_API_KEY}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        return {"error": err.response.json().get("error", {})}

def accedi_utente(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={st.secrets.FIREBASE_WEB_API_KEY}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as err:
        return {"error": err.response.json().get("error", {})}

def invia_email_verifica(id_token):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={st.secrets.FIREBASE_WEB_API_KEY}"
    payload = {"requestType": "VERIFY_EMAIL", "idToken": id_token}
    try:
        requests.post(url, json=payload)
    except requests.exceptions.HTTPError:
        pass

def elimina_utente(id_token):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:delete?key={st.secrets.FIREBASE_WEB_API_KEY}"
    payload = {"idToken": id_token}
    try:
        response = requests.post(url, json=payload); response.raise_for_status()
        return {"success": True}
    except requests.exceptions.HTTPError as err:
        error_message = err.response.json().get("error", {}).get("message", "ERRORE_SCONOSCIUTO")
        return {"error": error_message}

# --- Funzioni di Logica ---
@st.cache_data
def trova_distributori_google(citta=None, coordinate=None):
    api_key = st.secrets.GOOGLE_API_KEY
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
                transaction.update(doc_ref, {
                    f"prezzi.{tipo_carburante}.conferme": firestore.Increment(1),
                    f"prezzi.{tipo_carburante}.segnalato_da": firestore.ArrayUnion([user_id]),
                    "ultimo_aggiornamento": firestore.SERVER_TIMESTAMP
                })
                return True
            else:
                return False
        transaction = db.transaction()
        result = update_in_transaction(transaction, doc_ref)
        if result: st.success("Grazie per la tua conferma!")
        else: st.warning("Hai già confermato questo prezzo.")
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Errore durante la conferma: {e}")

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
                prezzo_val = info_carburante.get('valore', 'N/D')
                conferme_val = info_carburante.get('conferme', 0)
                testo_prezzi += f"<br><b>{carburante}: {prezzo_val} €</b> ({conferme_val} conferme)"
        popup_html = f"<strong>{distributore['nome']}</strong><br>{distributore['indirizzo']}{testo_prezzi}"
        if user_location:
            link_navigatore = f"https://www.google.com/maps/dir/?api=1&origin={user_location['latitude']},{user_location['longitude']}&destination={lat},{lon}"
            popup_html += f"<br><br><a href='{link_navigatore}' target='_blank'>➡️ Avvia Navigatore</a>"
        colore_icona = "green" if testo_prezzi else "blue"
        icona = folium.Icon(color=colore_icona, icon="gas-pump", prefix="fa")
        folium.Marker(location=[lat, lon], popup=popup_html, icon=icona).add_to(marker_cluster)

# --- INIZIO APP ---
if 'user_info' not in st.session_state: st.session_state.user_info = None
if 'distributori_trovati' not in st.session_state: st.session_state.distributori_trovati = []
if 'user_location' not in st.session_state: st.session_state.user_location = None

if not st.session_state.user_info:
    # Vista Login / Registrazione
    st.sidebar.header("Benvenuto!")
    scelta = st.sidebar.radio("Scegli un'azione:", ["Accedi", "Registrati"])
    email = st.sidebar.text_input("Email")
    password = st.sidebar.text_input("Password", type="password")
    if scelta == "Registrati":
        if st.sidebar.button("Registrati Ora"):
            if email and password:
                user_data = registra_utente(email, password)
                if "error" in user_data: st.sidebar.error(f"Errore: {user_data['error'].get('message', 'Sconosciuto')}")
                else:
                    id_token = user_data.get("idToken")
                    if id_token: invia_email_verifica(id_token)
                    st.sidebar.success("Registrazione avvenuta!"); st.sidebar.info("Ti abbiamo inviato un'email di verifica.")
            else: st.sidebar.warning("Inserisci email e password.")
    if scelta == "Accedi":
        if st.sidebar.button("Accedi"):
            if email and password:
                user_data = accedi_utente(email, password)
                if "error" in user_data: st.sidebar.error(f"Errore: {user_data['error'].get('message', 'Sconosciuto')}")
                else: st.session_state.user_info = user_data; st.rerun()
            else: st.sidebar.warning("Inserisci email e password.")
    st.info("👋 Benvenuto! Accedi o registrati dal menu a sinistra per usare l'app.")
else:
    # --- VISTA APP PRINCIPALE (per utenti loggati) ---
    st.sidebar.header(f"Benvenuto,")
    st.sidebar.write(st.session_state.user_info['email'])
    if st.sidebar.button("Logout"):
        st.session_state.user_info = None; st.cache_data.clear(); st.rerun()
    with st.sidebar.expander("⚠️ Gestione Account"):
        st.warning("Attenzione: l'eliminazione del tuo account è permanente.")
        if st.button("Elimina il mio account"):
            id_token = st.session_state.user_info.get("idToken")
            risultato = elimina_utente(id_token)
            if risultato.get("success"):
                st.session_state.user_info = None; st.success("Account eliminato."); st.balloons(); st.rerun()
            else:
                st.error(f"Errore: {risultato.get('error')}. Prova a fare Logout e Login e riprova.")

    with st.sidebar:
        st.header("📍 Trova Vicino a Me")
        location_data = streamlit_geolocation()
        if st.button("Usa la Mia Posizione"):
            if location_data:
                st.session_state.user_location = location_data
                st.session_state.distributori_trovati = trova_distributori_google(coordinate=location_data)
            else: st.warning("Posizione non trovata.")
    
    st.header("🌍 Cerca per Città")
    citta_cercata = st.text_input("Scrivi il nome di un comune:")
    if st.button("Cerca"):
        st.session_state.distributori_trovati = trova_distributori_google(citta=citta_cercata)

    if st.session_state.distributori_trovati:
        distributori = st.session_state.distributori_trovati
        prezzi_community = leggi_prezzi_da_firebase(distributori)
        st.markdown("---"); st.header("⛽ Risultati della Ricerca")
        
        tipi_carburante_trovati = list(set(carb for id, p_info in prezzi_community.items() for carb in p_info.get('prezzi', {}).keys()))
        carburante_selezionato = st.selectbox("Filtra per tipo di carburante:", ["-"] + sorted(tipi_carburante_trovati))
        
        risultati_finali = distributori
        con_prezzo = []
        if carburante_selezionato != "-":
            con_prezzo = [d for d in distributori if d['id'] in prezzi_community and carburante_selezionato in prezzi_community[d['id']].get('prezzi', {})]
            if con_prezzo:
                risultati_finali = sorted(con_prezzo, key=lambda d: float(prezzi_community[d['id']]['prezzi'][carburante_selezionato]['valore']))
        
        st.success(f"Trovati {len(distributori)} distributori. Visualizzo {len(risultati_finali)} risultati filtrati.")

        if risultati_finali:
            if carburante_selezionato != "-" and con_prezzo:
                lista_prezzi = [float(prezzi_community[d['id']]['prezzi'][carburante_selezionato]['valore']) for d in risultati_finali]
                if lista_prezzi:
                    st.subheader(f"📈 Statistiche per '{carburante_selezionato}' in zona")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Prezzo Minimo", f"{min(lista_prezzi):.3f} €"); col2.metric("Prezzo Massimo", f"{max(lista_prezzi):.3f} €"); col3.metric("Prezzo Medio", f"{sum(lista_prezzi) / len(lista_prezzi):.3f} €")
            
            tab_lista, tab_mappa = st.tabs(["🏆 Lista Risultati", "🗺️ Mappa"])

            with tab_lista:
                st.subheader("Lista dei distributori")
                for d in risultati_finali:
                    with st.container():
                        col_info, col_prezzo = st.columns([2, 1])
                        with col_info:
                            st.markdown(f"**{d['nome']}**<br><small>{d['indirizzo']}</small>", unsafe_allow_html=True)
                            if st.session_state.user_location:
                                link_navigatore = f"https://www.google.com/maps/dir/?api=1&origin={st.session_state.user_location['latitude']},{st.session_state.user_location['longitude']}&destination={d['latitudine']},{d['longitudine']}"
                                st.markdown(f"<a href='{link_navigatore}' target='_blank'>➡️ Avvia Navigatore</a>", unsafe_allow_html=True)
                        with col_prezzo:
                            prezzo_info_dict = prezzi_community.get(d['id'], {}).get('prezzi', {})
                            if carburante_selezionato != "-" and carburante_selezionato in prezzo_info_dict:
                                info_prezzo = prezzo_info_dict[carburante_selezionato]
                                st.metric(label=carburante_selezionato, value=f"{info_prezzo['valore']} €")
                                conferme = info_prezzo.get("conferme", 1)
                                st.write(f"✅ {conferme} Conferme")
                                user_id = st.session_state.user_info['localId']
                                if user_id not in info_prezzo.get("segnalato_da", []):
                                    if st.button("👍 Conferma", key=f"conf_{d['id']}"):
                                        conferma_prezzo(d['id'], carburante_selezionato, user_id)
                        st.markdown("---")
            
            with tab_mappa:
                mappa_citta = crea_mappa_base(centro=[float(risultati_finali[0]['latitudine']), float(risultati_finali[0]['longitudine'])], zoom=12)
                aggiungi_distributori_sulla_mappa(mappa_citta, risultati_finali, prezzi_community, user_location=st.session_state.user_location)
                st_folium(mappa_citta, width="100%", height=500, returned_objects=[])

        elif carburante_selezionato != "-":
             st.info(f"Nessun prezzo segnalato per '{carburante_selezionato}' in questa zona. Prova a segnalarne uno!")

    if st.session_state.distributori_trovati:
        st.markdown("---"); st.header("✍️ Segnala un Prezzo")
        distributori_per_form = st.session_state.distributori_trovati
        nomi_distributori = [d['nome'] for d in distributori_per_form]
        distributore_selezionato_nome = st.selectbox("1. Seleziona un distributore:", nomi_distributori)
        if distributore_selezionato_nome:
            distributore_selezionato_obj = [d for d in distributori_per_form if d['nome'] == distributore_selezionato_nome][0]
            id_selezionato = distributore_selezionato_obj['id']
            user_id = st.session_state.user_info['localId']
            col1, col2 = st.columns(2)
            with col1:
                carburante_da_segnalare = st.selectbox("3. Seleziona il carburante:", ["Benzina", "Gasolio", "GPL", "Metano"])
            with col2:
                prezzo_inserito = st.number_input("4. Inserisci il prezzo:", format="%.3f", step=0.001, min_value=0.0)
            if st.button("Invia Segnalazione"):
                if prezzo_inserito > 0:
                    salva_prezzo(id_selezionato, distributore_selezionato_nome, carburante_da_segnalare, prezzo_inserito, user_id)