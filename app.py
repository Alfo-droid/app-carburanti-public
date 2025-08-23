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
        if "universe_domain" in st.secrets["firebase_credentials"]:
            firebase_creds_dict["universe_domain"] = st.secrets["firebase_credentials"]["universe_domain"]
        cred = credentials.Certificate(firebase_creds_dict)
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error(f"⚠️ Errore di connessione a Firebase! Assicurati di aver impostato i Segreti correttamente. Dettagli: {e}")
    st.stop()

st.set_page_config(layout="wide")
st.title("⛽️ App Prezzi Carburante")

# --- Funzioni di Autenticazione ---
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

# MODIFICA 1: Aggiungiamo i campi per la gamification al profilo utente
def crea_profilo_utente(uid, email):
    db.collection("utenti").document(uid).set({
        "email": email,
        "data_registrazione": firestore.SERVER_TIMESTAMP,
        "privacy_accepted": False,
        "punti": 0,                   # <-- AGGIUNTO
        "numero_segnalazioni": 0      # <-- AGGIUNTO
    })

def get_profilo_utente(uid):
    doc_ref = db.collection("utenti").document(uid)
    doc = doc_ref.get()
    return doc.to_dict() if doc.exists else None

def accetta_privacy(uid):
    db.collection("utenti").document(uid).update({"privacy_accepted": True})

# --- NUOVA FUNZIONE PER I LIVELLI UTENTE ---
def get_livello_utente(punti):
    """
    Restituisce un titolo e un'icona in base ai punti dell'utente.
    """
    if punti < 50:
        return "Novellino", "👶"
    elif punti < 150:
        return "Contributore Attivo", "👍"
    elif punti < 500:
        return "Esperto del Rifornimento", "😎"
    elif punti < 1000:
        return "Re della Strada", "👑"
    else:
        return "Leggenda del Carburante", "🏆"
    
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

# MODIFICA 2: Assegniamo i punti quando si salva un prezzo
def salva_prezzo(id_distributore, nome_distributore, tipo_carburante, nuovo_prezzo, user_id):
    try:
        # Codice esistente per salvare il prezzo
        doc_ref = db.collection("prezzi_segnalati").document(id_distributore)
        nuovo_prezzo_data = {
            "valore": nuovo_prezzo, "conferme": 1,
            "segnalato_da": [user_id], "data_inserimento": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set({"id": id_distributore, "nome_distributore": nome_distributore, "prezzi": { tipo_carburante: nuovo_prezzo_data }, "ultimo_aggiornamento": firestore.SERVER_TIMESTAMP}, merge=True)

        # --- AGGIUNTA: Aggiornamento punti e segnalazioni utente ---
        utente_ref = db.collection("utenti").document(user_id)
        utente_ref.update({
            "punti": firestore.Increment(10),               # Aggiunge 10 punti
            "numero_segnalazioni": firestore.Increment(1)   # Aumenta di 1 le segnalazioni
        })
        # --- FINE AGGIUNTA ---

        st.success(f"Grazie! Prezzo per '{nome_distributore}' aggiornato."); st.cache_data.clear()
    except Exception as e: st.error(f"Errore durante il salvataggio: {e}")

# MODIFICA 3 (BONUS): Assegniamo punti anche per la conferma
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
        
        if result:
            # --- AGGIUNTA: Aggiornamento punti per la conferma ---
            utente_ref = db.collection("utenti").document(user_id)
            utente_ref.update({"punti": firestore.Increment(2)}) # Aggiunge 2 punti per la conferma
            # --- FINE AGGIUNTA ---
            st.success("Grazie per la tua conferma!")
        else:
            st.warning("Hai già confermato questo prezzo.")
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
                prezzo_val = info_carburante.get('valore', 'N/D'); conferme_val = info_carburante.get('conferme', 0)
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

# --- Sezione Sidebar ---
st.sidebar.header("👤 Area Utente")

# SE L'UTENTE NON È LOGGATO
if not st.session_state.user_info:
    st.sidebar.info("Accedi o registrati per contribuire!")
    scelta = st.sidebar.radio("Scegli un'azione:", ["Accedi", "Registrati"])
    email = st.sidebar.text_input("Email", key="login_email")
    password = st.sidebar.text_input("Password", type="password", key="login_password")
    
    if scelta == "Registrati":
        if st.sidebar.button("Registrati Ora"):
            if email and password:
                user_data = registra_utente(email, password)
                if "error" in user_data: 
                    st.sidebar.error(f"Errore: {user_data['error'].get('message', 'Sconosciuto')}")
                else:
                    id_token = user_data.get("idToken")
                    if id_token: 
                        invia_email_verifica(id_token)
                    st.sidebar.success("Registrazione avvenuta!")
                    st.sidebar.info("Ti abbiamo inviato un'email di verifica.")
            else: 
                st.sidebar.warning("Inserisci email e password.")
    
    if scelta == "Accedi":
        if st.sidebar.button("Accedi"):
            if email and password:
                user_data = accedi_utente(email, password)
                if "error" in user_data: 
                    st.sidebar.error(f"Errore: {user_data['error'].get('message', 'Sconosciuto')}")
                else: 
                    st.session_state.user_info = user_data
                    st.rerun()
            else: 
                st.sidebar.warning("Inserisci email e password.")

# SE L'UTENTE È LOGGATO (BLOCCO UNICO E CORRETTO)
else:
    user_id = st.session_state.user_info['localId']
    profilo_utente = get_profilo_utente(user_id)
    punti = profilo_utente.get('punti', 0) if profilo_utente else 0
    
    # Chiamiamo la nuova funzione per ottenere titolo 
    titolo, icona = get_livello_utente(punti)
    
    st.sidebar.write(f"Benvenuto, {st.session_state.user_info['email']}")
    st.sidebar.markdown("---")

    # Usiamo le colonne per un aspetto più ordinato
    col1, col2 = st.sidebar.columns(2)
    with col1:
        st.metric(label="🏆 Punteggio", value=punti)
    with col2:
        st.metric(label="✨ Livello", value=titolo) # Ho notato che hai tolto l'icona, va benissimo!

    st.sidebar.markdown("---")

    if st.sidebar.button("Logout"):
        st.session_state.user_info = None
        st.cache_data.clear()
        st.rerun()
    
    with st.sidebar.expander("⚠️ Gestione Account"):
        st.warning("Attenzione: l'eliminazione del tuo account è permanente.")
        if st.button("Elimina il mio account"):
            id_token = st.session_state.user_info.get("idToken")
            risultato = elimina_utente(id_token)
            if risultato.get("success"):
                st.session_state.user_info = None
                st.success("Account eliminato.")
                st.balloons()
                st.rerun()
            else:
                st.error(f"Errore: {risultato.get('error')}. Prova a fare Logout e Login e riprova.")

# --- Il resto del codice rimane invariato, lo includo per completezza ---
# --- Logica di Visualizzazione ---
privacy_accettata = False
if st.session_state.user_info:
    profilo_utente_main = get_profilo_utente(st.session_state.user_info['localId']) # Riusiamo la funzione
    if profilo_utente_main and profilo_utente_main.get("privacy_accepted", False):
        privacy_accettata = True
    else:
        st.subheader("Informativa sulla Privacy")
        st.info("Benvenuto! Per usare le funzioni di contribuzione, devi accettare la nostra informativa.")
        st.write("Raccoglieremo la tua email per l'account e tracceremo i prezzi che segnali per garantire la qualità del servizio.")
        accettato = st.checkbox("Dichiaro di aver letto e accettato l'informativa sulla privacy.")
        if st.button("Continua", disabled=not accettato):
            accetta_privacy(st.session_state.user_info['localId']); st.rerun()
else:
    privacy_accettata = True

if privacy_accettata:
    with st.sidebar:
        st.markdown("---")
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
            
            tab_lista, tab_mappa, tab_classifica = st.tabs(["🏆 Lista Risultati", "🗺️ Mappa", "🥇 Classifica"])

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
                                if st.session_state.user_info:
                                    user_id = st.session_state.user_info['localId']
                                    if user_id not in info_prezzo.get("segnalato_da", []):
                                        if st.button("👍 Conferma", key=f"conf_{d['id']}"):
                                            conferma_prezzo(d['id'], carburante_selezionato, user_id)
                    st.markdown("---")
            
            with tab_mappa:
                if risultati_finali:
                    mappa_citta = crea_mappa_base(centro=[float(risultati_finali[0]['latitudine']), float(risultati_finali[0]['longitudine'])], zoom=12)
                    aggiungi_distributori_sulla_mappa(mappa_citta, risultati_finali, prezzi_community, user_location=st.session_state.user_location)
                    st_folium(mappa_citta, width="100%", height=500, returned_objects=[])
                    # --- BLOCCO NUOVO PER LA TAB CLASSIFICA ---
with tab_classifica:
    st.subheader("🏆 Top 10 Utenti della Community")
    
    # Chiamiamo la funzione per ottenere i dati
    top_utenti = get_top_utenti()
    
    if not top_utenti:
        st.info("Non c'è ancora nessuno in classifica. Sii il primo a contribuire!")
    else:
        # Creiamo un'intestazione per la classifica
        col1, col2, col3 = st.columns([1, 4, 2])
        col1.write("**Pos.**")
        col2.write("**Utente**")
        col3.write("**Punti**")
        st.markdown("---")

        # Mostriamo ogni utente in classifica
        for i, utente in enumerate(top_utenti):
            col1, col2, col3 = st.columns([1, 4, 2])
            
            # Aggiungiamo le medaglie per i primi 3
            posizione = i + 1
            if posizione == 1:
                col1.markdown(f"🥇 **{posizione}**")
            elif posizione == 2:
                col1.markdown(f"🥈 **{posizione}**")
            elif posizione == 3:
                col1.markdown(f"🥉 **{posizione}**")
            else:
                col1.write(str(posizione))

            col2.write(utente['nome'])
            col3.write(f"**{utente['punti']}**")

        elif carburante_selezionato != "-":
                 st.info(f"Nessun prezzo segnalato per '{carburante_selezionato}' in questa zona.")

    if st.session_state.user_info and st.session_state.distributori_trovati:
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
    elif st.session_state.distributori_trovati and not st.session_state.user_info:
        st.info("💡 Accedi o registrati per poter segnalare e confermare i prezzi!")