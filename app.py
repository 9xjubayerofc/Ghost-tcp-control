import os
import sys
import subprocess
import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ইউজার সেশন ডাটা স্টোর করার জন্য ডিকশনারি
user_sessions = {} 
ADMIN_CONFIG = "admin_config.txt"

# অ্যাডমিন কনফিগারেশন লোড করা
def get_config():
    conf = {"pass": "admin123", "duration": 120} # ডিফল্ট ভ্যালু
    if os.path.exists(ADMIN_CONFIG):
        with open(ADMIN_CONFIG, 'r') as f:
            for line in f:
                if '=' in line:
                    key, val = line.strip().split('=')
                    if key == 'admin_password': conf['pass'] = val
                    if key == 'global_duration': conf['duration'] = int(val)
    return conf

# অ্যাডমিন কনফিগারেশন সেভ করা
def save_config(password, duration):
    with open(ADMIN_CONFIG, 'w') as f:
        f.write(f"admin_password={password}\nglobal_duration={duration}\n")

# বটের এক্সপায়ারি চেক করার জন্য মনিটর থ্রেড
def expiry_monitor():
    while True:
        now = datetime.now()
        for name, data in list(user_sessions.items()):
            if data['running'] and data['end_time'] != "unlimited":
                if now > data['end_time']:
                    if data['proc']:
                        data['proc'].terminate()
                    user_sessions[name]['running'] = False
                    socketio.emit('status_update', {'running': False, 'user': name})
        time.sleep(2) # চেক ইন্টারভাল একটু কমানো হয়েছে দ্রুত রেসপন্সের জন্য

threading.Thread(target=expiry_monitor, daemon=True).start()

# বটের লগ স্ট্রিম করার ফাংশন
def stream_logs(proc, name):
    try:
        for line in iter(proc.stdout.readline, ''):
            if line:
                socketio.emit('new_log', {'data': line.strip(), 'user': name})
        proc.stdout.close()
    except Exception as e:
        print(f"Logging error for {name}: {e}")

@app.route('/')
def index():
    return render_template('index.html')

# --- রিফ্রেশ করার পর স্ট্যাটাস চেক করার এন্ডপয়েন্ট (Fixed) ---
@app.route('/api/check_status', methods=['POST'])
def check_status():
    data = request.json
    name = data.get('name')
    if name in user_sessions and user_sessions[name]['running']:
        info = user_sessions[name]
        # সেকেন্ড ক্যালকুলেশন (Days সহ দেখানোর জন্য সেকেন্ড পাঠানো জরুরি)
        if info['end_time'] == "unlimited":
            rem_sec = -1
        else:
            rem_sec = int((info['end_time'] - datetime.now()).total_seconds())
        
        return jsonify({
            "running": True, 
            "rem_sec": max(0, rem_sec)
        })
    return jsonify({"running": False})

# --- বট স্টার্ট/স্টপ কন্ট্রোল (bot.txt লজিক সহ) ---
@app.route('/api/control', methods=['POST'])
def bot_control():
    data = request.json
    action = data.get('action')
    name = data.get('name')
    uid = data.get('uid')
    pw = data.get('password')
    conf = get_config()

    if action == 'start':
        if not uid or not pw:
            return jsonify({"status": "error", "message": "UID and Password are required! Please save in Account Setup."})

        if name in user_sessions and user_sessions[name]['running']:
            return jsonify({"status": "error", "message": "ALREADY RUNNING!"})
        
        # --- bot.txt ফাইলে ইউজার ডাটা নির্দিষ্ট ফরম্যাটে সেভ করা ---
        try:
            with open("bot.txt", "w") as f:
                f.write(f"uid={uid}\npassword={pw}")
            
            # main.py ফাইলটি সাব-প্রসেস হিসেবে চালু করা
            proc = subprocess.Popen(
                [sys.executable, 'main.py'], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                text=True, 
                bufsize=1, 
                universal_newlines=True
            )
            
            # টাইম ক্যালকুলেশন
            if conf['duration'] == -1:
                end_time = "unlimited"
                rem_sec = -1
            else:
                end_time = datetime.now() + timedelta(minutes=conf['duration'])
                rem_sec = conf['duration'] * 60
            
            user_sessions[name] = {
                'proc': proc, 
                'end_time': end_time, 
                'running': True
            }
            
            # লগ থ্রেড শুরু
            threading.Thread(target=stream_logs, args=(proc, name), daemon=True).start()
            
            return jsonify({
                "status": "success", 
                "message": "BOT STARTED", 
                "running": True, 
                "rem_sec": rem_sec
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    elif action == 'stop':
        if name in user_sessions and user_sessions[name]['running']:
            if user_sessions[name]['proc']:
                user_sessions[name]['proc'].terminate()
            user_sessions[name]['running'] = False
            return jsonify({"status": "success", "message": "BOT STOPPED", "running": False})
        return jsonify({"status": "error", "message": "NOT RUNNING!"})

    return jsonify({"status": "error", "message": "INVALID ACTION"})

# --- অ্যাডমিন এপিআই (পূর্ণাঙ্গ) ---
@app.route('/api/admin', methods=['POST'])
def admin_api():
    data = request.json
    conf = get_config()
    
    if data.get('password') != conf['pass']:
        return jsonify({"status": "error", "message": "Wrong Admin Passkey!"})
    
    action = data.get('action')
    
    if action == 'login':
        active_users = []
        for name, info in user_sessions.items():
            if info['running']:
                if info['end_time'] == "unlimited":
                    rem = -1
                else:
                    rem = max(0, int((info['end_time'] - datetime.now()).total_seconds() / 60))
                active_users.append({"name": name, "rem_min": rem})
        return jsonify({"status": "success", "duration": conf['duration'], "users": active_users})
    
    elif action == 'save_global':
        new_dur = int(data.get('duration', 120))
        save_config(conf['pass'], new_dur)
        return jsonify({"status": "success", "message": "Global limit updated!"})

    elif action == 'update_user_time':
        user_name = data.get('user')
        new_mins = int(data.get('mins', 0))
        if user_name in user_sessions:
            user_sessions[user_name]['end_time'] = datetime.now() + timedelta(minutes=new_mins)
            new_rem_sec = new_mins * 60
            socketio.emit('time_sync', {'rem_sec': new_rem_sec, 'user': user_name})
            return jsonify({"status": "success"})
            
    elif action == 'stop_user':
        user_name = data.get('user')
        if user_name in user_sessions and user_sessions[user_name]['running']:
            user_sessions[user_name]['proc'].terminate()
            user_sessions[user_name]['running'] = False
            socketio.emit('status_update', {'running': False, 'user': user_name})
            return jsonify({"status": "success"})

    return jsonify({"status": "error"})

# --- ক্ল্যান ও গিল্ড প্রক্সি এপিআই ---
@app.route('/api/proxy_guild')
def proxy_guild():
    t = request.args.get('type')
    gid = request.args.get('guild_id')
    reg = request.args.get('region')
    uid = request.args.get('uid')
    pw = request.args.get('password')
    
    base_url = "https://guild-info-danger.vercel.app"
    urls = {
        'info': f"{base_url}/guild?guild_id={gid}&region={reg}",
        'join': f"{base_url}/join?guild_id={gid}&uid={uid}&password={pw}",
        'members': f"{base_url}/members?guild_id={gid}&uid={uid}&password={pw}",
        'leave': f"{base_url}/leave?guild_id={gid}&uid={uid}&password={pw}"
    }
    
    try:
        resp = requests.get(urls.get(t), timeout=15)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": "External API Error", "details": str(e)})

if __name__ == '__main__':
    # সার্ভার রান করা
    socketio.run(app, debug=True, port=5004, host='0.0.0.0')
    def wrap(*args, **kwargs):
        if 'logged_in' in session:
            return f(*args, **kwargs)
        return redirect(url_for('login'))
    wrap.__name__ = f.__name__
    return wrap

# বটের এক্সপায়ারি চেক করার মনিটর
def expiry_monitor():
    while True:
        now = datetime.now()
        for name, data in list(user_sessions.items()):
            if data['running'] and data['end_time'] != "unlimited":
                if now > data['end_time']:
                    if data['proc']:
                        data['proc'].terminate()
                    user_sessions[name]['running'] = False
                    socketio.emit('status_update', {'running': False, 'user': name})
        time.sleep(2)

threading.Thread(target=expiry_monitor, daemon=True).start()

def stream_logs(proc, name):
    try:
        for line in iter(proc.stdout.readline, ''):
            if line:
                socketio.emit('new_log', {'data': line.strip(), 'user': name})
        proc.stdout.close()
    except Exception as e:
        print(f"Logging error for {name}: {e}")

# --- রুটস (Routes) ---

@app.route('/login')
def login():
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template_string(LOGIN_HTML)

@app.route('/api/login_auth', methods=['POST'])
def login_auth():
    data = request.json
    u = data.get('username')
    p = data.get('password')
    # ছবিতে দেখানো ডিফল্ট পাসওয়ার্ড অনুযায়ী চেক
    if u == "admin" and p == "changeme123":
        session['logged_in'] = True
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Invalid credentials!"})

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- বটের মূল কন্ট্রোল এপিআই ---

@app.route('/api/check_status', methods=['POST'])
@login_required
def check_status():
    data = request.json
    name = data.get('name')
    if name in user_sessions and user_sessions[name]['running']:
        info = user_sessions[name]
        rem_sec = -1 if info['end_time'] == "unlimited" else int((info['end_time'] - datetime.now()).total_seconds())
        return jsonify({"running": True, "rem_sec": max(0, rem_sec)})
    return jsonify({"running": False})

@app.route('/api/control', methods=['POST'])
@login_required
def bot_control():
    data = request.json
    action, name, uid, pw = data.get('action'), data.get('name'), data.get('uid'), data.get('password')
    conf = get_config()

    if action == 'start':
        if not uid or not pw:
            return jsonify({"status": "error", "message": "UID/PW required!"})
        if name in user_sessions and user_sessions[name]['running']:
            return jsonify({"status": "error", "message": "ALREADY RUNNING!"})
        try:
            with open("bot.txt", "w") as f: f.write(f"uid={uid}\npassword={pw}")
            proc = subprocess.Popen([sys.executable, 'main.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            end_time = "unlimited" if conf['duration'] == -1 else datetime.now() + timedelta(minutes=conf['duration'])
            user_sessions[name] = {'proc': proc, 'end_time': end_time, 'running': True}
            threading.Thread(target=stream_logs, args=(proc, name), daemon=True).start()
            return jsonify({"status": "success", "running": True, "rem_sec": (conf['duration']*60 if conf['duration'] != -1 else -1)})
        except Exception as e: return jsonify({"status": "error", "message": str(e)})

    elif action == 'stop':
        if name in user_sessions and user_sessions[name]['running']:
            if user_sessions[name]['proc']: user_sessions[name]['proc'].terminate()
            user_sessions[name]['running'] = False
            return jsonify({"status": "success", "running": False})
    return jsonify({"status": "error", "message": "FAILED"})

# অ্যাডমিন ও প্রক্সি এপিআই আগের মতই রয়েছে...
@app.route('/api/admin', methods=['POST'])
@login_required
def admin_api():
    data = request.json
    conf = get_config()
    if data.get('password') != conf['pass']: return jsonify({"status": "error", "message": "Wrong Passkey!"})
    action = data.get('action')
    if action == 'login':
        active_users = [{"name": n, "rem_min": (-1 if i['end_time'] == "unlimited" else max(0, int((i['end_time'] - datetime.now()).total_seconds() / 60)))} for n, i in user_sessions.items() if i['running']]
        return jsonify({"status": "success", "duration": conf['duration'], "users": active_users})
    elif action == 'save_global':
        save_config(conf['pass'], int(data.get('duration', 120)))
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/proxy_guild')
@login_required
def proxy_guild():
    t, gid, reg, uid, pw = request.args.get('type'), request.args.get('guild_id'), request.args.get('region'), request.args.get('uid'), request.args.get('password')
    base_url = "https://guild-info-danger.vercel.app"
    urls = {
        'info': f"{base_url}/guild?guild_id={gid}&region={reg}",
        'join': f"{base_url}/join?guild_id={gid}&uid={uid}&password={pw}",
        'members': f"{base_url}/members?guild_id={gid}&uid={uid}&password={pw}",
        'leave': f"{base_url}/leave?guild_id={gid}&uid={uid}&password={pw}"
    }
    try:
        resp = requests.get(urls.get(t), timeout=15)
        return jsonify(resp.json())
    except: return jsonify({"error": "API Error"})

import os

if __name__ == '__main__':
    # Render
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host='0.0.0.0', port=port)
