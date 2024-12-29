from nltk.tokenize import word_tokenize, sent_tokenize
from nltk.corpus import wordnet, stopwords
from nltk import ne_chunk, pos_tag
from nltk.sentiment import SentimentIntensityAnalyzer
from nltk import FreqDist
import numpybÁ
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory, jsonify
from flask_socketio import SocketIO, emit, join_room
import os
import json
import sqlite3
from datetime import timedelta
from authlib.integrations.flask_client import OAuth

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_unique_secret_key'  # Replace with a secure key

# Initialize SocketIO
socketio = SocketIO(app)

# Folder configurations
UPLOAD_FOLDER = 'uploads'
MUSIC_FOLDER = '/storage/emulated/0/VidMate/download'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# OAuth Configuration
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id='YOUR_CLIENT_ID',
    client_secret='YOUR_CLIENT_SECRET',
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    client_kwargs={'scope': 'openid email profile'}
)

# SQLite database initialization
conn = sqlite3.connect('chat.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT,
    username TEXT,
    message TEXT
)
''')
conn.commit()

# JSON database file
USER_DB_FILE = 'users.json'

# Ensure the JSON file exists
if not os.path.exists(USER_DB_FILE):
    with open(USER_DB_FILE, 'w') as f:
        json.dump({}, f)

# Helper functions to read/write user data
def load_users():
    with open(USER_DB_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USER_DB_FILE, 'w') as f:
        json.dump(users, f)

# Routes
@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = load_users()

        if username in users:
            flash("Username already exists. Try a different one.")
            return redirect(url_for('signup'))

        users[username] = password
        save_users(users)
        flash("Signup successful. Please log in.")
        return redirect(url_for('login'))
    return render_template('signup.html')

app.permanent_session_lifetime = timedelta(days=30)  # Session lasts for 30 days

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        users = load_users()

        if username in users and users[username] == password:
            session.permanent = True  # Make the session permanent
            session['username'] = username
            flash("Login successful!")
            return redirect(url_for('dashboard'))
        flash("Invalid username or password.")
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash("You have been logged out.")
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        flash("Please log in to access the dashboard.")
        return redirect(url_for('login'))
    return render_template('main.html', username=session['username'])

@app.route('/drive', methods=['GET', 'POST'])
def drive():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash("No file part in the request!")
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash("No file selected for upload!")
            return redirect(request.url)
        
        # Validate file extension
        allowed_extensions = ('.mp3', '.wav', '.txt', '.pdf', '.jpg', '.png', '.mp4', '.mkv', '.avi')
        if not file.filename.lower().endswith(allowed_extensions):
            flash("Invalid file type! Only specific formats are allowed.")
            return redirect(request.url)
        
        # Save file to UPLOAD_FOLDER
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        flash(f"File {file.filename} uploaded successfully!")

    # List files dynamically from UPLOAD_FOLDER
    files = os.listdir(app.config['UPLOAD_FOLDER'])
    categorized_files = {
        'video': [f for f in files if f.lower().endswith(('.mp4', '.mkv', '.avi'))],
        'music': [f for f in files if f.lower().endswith(('.mp3', '.wav'))],
        'image': [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
        'pdf': [f for f in files if f.lower().endswith('.pdf')],
        'text': [f for f in files if f.lower().endswith('.txt')],
        'other': [f for f in files if not f.lower().endswith(('.mp4', '.mp3', '.wav', '.jpg', '.jpeg', '.png', '.pdf', '.txt'))]
    }

    return render_template('drive.html', categorized_files=categorized_files)

@app.route('/downloads/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/open/<filename>')
def open_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    else:
        flash(f"File {filename} not found.")
        return redirect(url_for('drive'))

@app.route('/music/<path:filename>')
def serve_music(filename):
    file_path = os.path.join("/storage/emulated/0/VidMate/download/", filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    else:
        return f"File {filename} not found.", 404

connected_users = []  # To store connected users

@app.route('/sync', methods=['GET', 'POST'])
def sync():
    music_files = [f for f in os.listdir(MUSIC_FOLDER) if f.endswith(('.mp3', '.wav'))]

    if request.method == 'POST':
        username = request.form.get('username')  # Username from login
        user_role = request.form.get('role')    # Get user role (Player or Speaker)
        selected_music = request.form.get('music_file')  # Get selected music file
        sync_time_gap = request.form.get('sync_time_gap', 0)  # Sync time gap adjustment (in milliseconds)

        if username:
            # Add user to the connected users list
            connected_users.append({'username': username, 'role': user_role})
            # Notify all clients about the new user joining
            socketio.emit('user_joined', {'username': username, 'role': user_role}, broadcast=True)

        if user_role and selected_music:
            if user_role == 'Player':
                # Emit selected music and sync data to all connected clients
                socketio.emit('music_data', {'music_file': selected_music, 'sync_time_gap': sync_time_gap}, broadcast=True)
                flash("You are the player, syncing music...")
            else:
                flash("You are the speaker, waiting for the music to be played...")
        else:
            flash("Please select both a role and a music file.")

    return render_template('sync.html', files=music_files)

# SocketIO event for new connection
@socketio.on('join_club')
def handle_join_club(data):
    username = data.get('username')
    role = data.get('role')
    if username:
        # Add user to the connected users list
        connected_users.append({'username': username, 'role': role})
        # Notify only the Player about the joined user
        for user in connected_users:
            if user['role'] == 'Player':
                emit('update_users', {'users': connected_users}, to=request.sid)

# Allow player to fetch the connected users (show only Speakers to the Player)
@socketio.on('get_users')
def send_connected_users():
    # Filter the users to only show Speakers to Players
    speakers = [user for user in connected_users if user['role'] == 'Speaker']
    emit('update_users', {'users': speakers})
    
@socketio.on('music_data')
def handle_music_data(data):
    music_file = data.get('music_file')
    sync_time_gap = data.get('sync_time_gap', 0)  # Get the sync time gap adjustment
    if music_file:
        # Broadcast the music file and sync time gap to all connected clients
        emit('play_music', {'music_file': music_file, 'sync_time_gap': sync_time_gap}, broadcast=True)

@socketio.on('play_music')
def play_music(data):
    music_file = data.get('music_file')
    sync_time_gap = data.get('sync_time_gap', 0)  # Sync time gap for player-speaker sync
    if music_file:
        # Broadcast the music file and sync time gap to all clients
        emit('music_data', {'music_file': music_file, 'sync_time_gap': sync_time_gap}, broadcast=True)
        
@app.route('/whatschat', methods=['GET', 'POST'])
def whatschat():
    return render_template('whatschat.html')

@socketio.on('join_room')
def handle_join(data):
    username = data.get('username')
    room = data.get('room')
    join_room(room)
    if room not in rooms:
        rooms[room] = []
    if username not in rooms[room]:
        rooms[room].append(username)
    emit('user_joined', {'room': room, 'users': rooms[room]}, room=room)

@socketio.on('send_message')
def handle_message(data):
    room = data.get('room')
    message = data.get('message')
    username = data.get('username')
    cursor.execute("INSERT INTO chat (room, username, message) VALUES (?, ?, ?)", (room, username, message))
    conn.commit()
    emit('new_message', {'username': username, 'message': message}, room=room)



# Need Feedback Page
@app.route("/need", methods=["GET", "POST"])
def need():
    if request.method == "POST":
        feedback = request.form.get("feedback")
        f=open('feedback.txt','a')
        feed=feedback+"\n"
        f.write(feed)
        print(feedback)
        f.close()
        return f"Thank you for your feedback: {feedback}"
        
    return render_template("need.html")
    
# Error Handling
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
    

    
#-----------------------------------------AI work Zone#-----------------------------------------#

# Initialize Flask app


# NLTK Sentiment Analyzer
sia = SentimentIntensityAnalyzer()

# NLTK functionalities
def word_tokenize_text(text):
    return word_tokenize(text)

def sentence_tokenize_text(text):
    return sent_tokenize(text)

def analyze_word_frequency(text):
    words = word_tokenize(text)
    stop_words = set(stopwords.words('english'))
    filtered_words = [word for word in words if word.lower() not in stop_words]
    freq_dist = FreqDist(filtered_words)
    return dict(freq_dist)

def find_synonyms(word):
    synonyms = wordnet.synsets(word)
    return [syn.lemmas()[0].name() for syn in synonyms]

def named_entity_recognition(text):
    words = word_tokenize(text)
    tagged = pos_tag(words)
    named_entities = ne_chunk(tagged)
    return str(named_entities)

def sentiment_analysis(text):
    return sia.polarity_scores(text)

# AI Zone route
@app.route('/AI_Zone', methods=["GET", "POST"])
def ai_zone():
    if request.method == 'POST':
        data = request.json
        choice = data.get('choice')
        text = data.get('text', '')
        word = data.get('word', '')

        if choice == '1':
            result = word_tokenize_text(text)
        elif choice == '2':
            result = sentence_tokenize_text(text)
        elif choice == '3':
            result = analyze_word_frequency(text)
        elif choice == '4':
            result = find_synonyms(word)
        elif choice == '5':
            result = named_entity_recognition(text)
        elif choice == '6':
            result = sentiment_analysis(text)
        else:
            result = {"error": "Invalid choice"}

        return jsonify(result)  # Return JSON response for AJAX requests
    return render_template('AI_Zone.html')  # Render template for GET requests


#--------------------------------------AI work Zone End-------------------------------------#
# Main Execution
if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
