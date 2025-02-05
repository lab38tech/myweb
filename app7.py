from flask import Flask, render_template, request, redirect, url_for, session, send_file, send_from_directory, flash
from flask_mail import Mail, Message
import os
import csv
import re
import googleapiclient.discovery
import requests
import logging
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# Configurazione email
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

# Configurazione Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# URL del file JSON
url = 'https://www.lab38.it/credentials.json'

# Leggi il file JSON da remoto
response = requests.get(url)

# Verifica che la richiesta sia andata a buon fine
if response.status_code == 200:
    # Carica il file JSON direttamente in memoria
    credentials_data = response.json()

    # Usa le credenziali direttamente in memoria
    creds = service_account.Credentials.from_service_account_info(
        credentials_data,
        scopes=SCOPES
    )
else:
    logger.error("Errore nel recuperare il file: ", response.status_code)
    creds = None

USERS_CSV = 'users.csv'

# Configurazione logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_folder_id(drive_link):
    patterns = [
        r'/folders/([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
        r'([a-zA-Z0-9_-]{25,})'
    ]
    for pattern in patterns:
        match = re.search(pattern, drive_link)
        if match:
            return match.group(1)
    return None

def load_users():
    users = []
    if os.path.exists(USERS_CSV):
        with open(USERS_CSV, 'r') as f:
            reader = csv.DictReader(f)
            users = list(reader)
    return users

def save_users(users):
    fieldnames = ['username', 'password', 'drive_folder_id']
    with open(USERS_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(users)

def get_drive_images(folder_id):
    try:
        service = googleapiclient.discovery.build('drive', 'v3', credentials=creds)
        
        results = service.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image'",
            fields='files(id, name)',
            pageSize=1000
        ).execute()
        
        return [{
            'id': file['id'],
            'name': file['name'],
            'url': f"/proxy/{file['id']}"
        } for file in results.get('files', [])]
    
    except Exception as e:
        logger.error(f"Errore Google Drive API: {str(e)}")
        return []

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        users = load_users()
        
        # Controllo accesso admin
        if username == os.getenv('ADMIN_USERNAME') and password == os.getenv('ADMIN_PASSWORD'):
            session['user'] = username
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        
        user = next((u for u in users if u['username'] == username and u['password'] == password), None)
        
        if user:
            session['user'] = username
            session['drive_folder'] = user['drive_folder_id']
            session['is_admin'] = False
            return redirect(url_for('gallery'))
            
        flash('Credenziali non valide', 'error')
        return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    
    users = load_users()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            username = request.form['username']
            password = request.form['password']
            drive_link = request.form['drive_link']
            
            if username == os.getenv('ADMIN_USERNAME'):
                flash('Nome utente riservato', 'error')
                return redirect(url_for('admin_panel'))
            
            folder_id = extract_folder_id(drive_link)
            if not folder_id:
                flash('Link Google Drive non valido', 'error')
                return redirect(url_for('admin_panel'))
                
            if any(u['username'] == username for u in users):
                flash('Utente già esistente', 'error')
                return redirect(url_for('admin_panel'))
                
            users.append({
                'username': username,
                'password': password,
                'drive_folder_id': folder_id
            })
            
            save_users(users)
            flash('Utente aggiunto con successo', 'success')
        
        elif action == 'delete':
            username = request.form['username']
            if username == os.getenv('ADMIN_USERNAME'):
                flash('Impossibile eliminare admin', 'error')
                return redirect(url_for('admin_panel'))
                
            users = [u for u in users if u['username'] != username]
            save_users(users)
            flash('Utente eliminato', 'success')
    
    return render_template('admin.html', users=users)

@app.route('/gallery')
def gallery():
    if 'user' not in session or 'drive_folder' not in session:
        return redirect(url_for('login'))
    
    try:
        images = get_drive_images(session['drive_folder'])
        if not images:
            return render_template('error.html', message="Nessuna immagine trovata"), 404
            
        return render_template('gallery.html', images=images)
    except Exception as e:
        logger.error(f"Errore galleria: {str(e)}")
        return render_template('error.html', message="Errore nel caricamento"), 500


@app.route('/selection', methods=['POST'])
def process_selection():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    try:
        selected_ids = request.form.getlist('selected_images')
        if not selected_ids:
            return redirect(url_for('gallery'))
        
        session['selected_ids'] = selected_ids
        return redirect(url_for('show_selection'))
        
    except Exception as e:
        logger.error(f"Errore selezione: {str(e)}")
        return render_template('error.html', message="Errore nell'elaborazione della selezione"), 500

@app.route('/selection')
def show_selection():
    if 'user' not in session or 'selected_ids' not in session:
        return redirect(url_for('login'))
    
    try:
        all_images = get_drive_images(session['drive_folder'])
        selected_images = [img for img in all_images if img['id'] in session['selected_ids']]
        
        return render_template('selection.html', 
                            selected_images=selected_images,
                            count=len(selected_images))
    except Exception as e:
        logger.error(f"Errore visualizzazione selezione: {str(e)}")
        return render_template('error.html', message="Errore nel caricamento della selezione"), 500

@app.route('/send_email', methods=['POST'])
def send_email():
    if 'user' not in session or 'selected_ids' not in session:
        return redirect(url_for('login'))
    
    try:
        user = session['user']
        all_images = get_drive_images(session['drive_folder'])
        selected_images = [img for img in all_images if img['id'] in session['selected_ids']]
        
        # Prepara email
        email_body = "Immagini selezionate:\n\n" + '\n'.join(
            [f"{i+1}. {img['name']} (ID: {img['id']})" for i, img in enumerate(selected_images)]
        )
        
        msg = Message(
            subject=f"Selezione immagini di {user}",
            sender=(user, app.config['MAIL_USERNAME']),
            recipients=[os.getenv('ADMIN_EMAIL')],
            body=email_body
        )
        mail.send(msg)
        
        # Reset sessione
        session.pop('selected_ids', None)
        return render_template('confirmation.html', count=len(selected_images))
        
    except Exception as e:
        logger.error(f"Errore invio email: {str(e)}")
        return render_template('error.html', message="Errore nell'invio dell'email"), 500


@app.route('/proxy/<file_id>')
def drive_proxy(file_id):
    """Endpoint proxy per le immagini"""
    try:
        url = f'https://drive.google.com/uc?export=download&id={file_id}'
        response = requests.get(url, stream=True, timeout=10)
        return send_file(response.raw, mimetype=response.headers.get('Content-Type'))
    
    except Exception as e:
        logger.error(f"Errore proxy: {str(e)}")
        return "Errore nel caricamento dell'immagine", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    if not os.path.exists(USERS_CSV):
        with open(USERS_CSV, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['username', 'password', 'drive_folder_id'])
    
    app.run(
        host=os.getenv('HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', 5000)),
        debug=os.getenv('FLASK_DEBUG', 'False') == 'True'
    )
