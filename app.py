import os, sys, subprocess, threading, time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

user_processes = {} 
ADMIN_CONFIG = "admin_config.txt"

def get_config():
    conf = {"pass": "admin123", "duration": 120}
    if os.path.exists(ADMIN_CONFIG):
        with open(ADMIN_CONFIG, 'r') as f:
            lines = f.readlines()
            for line in lines:
                if 'admin_password' in line: conf['pass'] = line.split('=')[1].strip()
                if 'global_duration' in line: 
                    val = line.split('=')[1].strip()
                    conf['duration'] = -1 if val == "unlimited" else int(val)
    return conf

def save_config(password, duration):
    with open(ADMIN_CONFIG, 'w') as f:
        f.write(f"admin_password={password}\nglobal_duration={duration}\n")

def stream_logs(proc, name):
    for line in iter(proc.stdout.readline, ''):
        if line:
            socketio.emit('new_log', {'data': line.strip(), 'user': name})
    proc.stdout.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/control', methods=['POST'])
def bot_control():
    data = request.json
    action, name, uid, pw = data.get('action'), data.get('name'), data.get('uid'), data.get('password')
    conf = get_config()

    if action in ["start", "restart"]:
        if name in user_processes: user_processes[name]['proc'].terminate()
        try:
            with open("black_apis.txt", "w") as f: f.write(f"uid={uid}\npassword={pw}\n")
            proc = subprocess.Popen([sys.executable, 'BLACK_Apis.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
            end_time = "unlimited" if conf['duration'] == -1 else datetime.now() + timedelta(minutes=conf['duration'])
            user_processes[name] = {'proc': proc, 'end_time': end_time}
            threading.Thread(target=stream_logs, args=(proc, name), daemon=True).start()
            return jsonify({"status": "success", "message": "SYSTEM INITIALIZED!", "running": True})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    
    elif action == "stop" and name in user_processes:
        user_processes[name]['proc'].terminate()
        return jsonify({"status": "success", "message": "SYSTEM HALTED!", "running": False})
    
    elif action == "check_status":
        is_running = name in user_processes and user_processes[name]['proc'].poll() is None
        return jsonify({"running": is_running})

    return jsonify({"status": "error", "message": "INVALID ACTION"})

@app.route('/api/admin', methods=['POST'])
def admin():
    data = request.json
    conf = get_config()
    if str(data.get('password')).strip() != str(conf['pass']).strip():
        return jsonify({"status": "error", "message": "WRONG PASSKEY!"})
    
    action = data.get('action')
    if action == "get_stats":
        active = [{"name": n, "rem": "INF" if d['end_time'] == "unlimited" else round((d['end_time'] - datetime.now()).total_seconds() / 60, 1)} 
                  for n, d in user_processes.items() if d['proc'].poll() is None]
        return jsonify({"status": "success", "users": active, "global_dur": conf['duration']})
    
    if action == "set_global":
        new_dur = data.get('duration')
        save_config(conf['pass'], new_dur)
        return jsonify({"status": "success", "message": f"GLOBAL TIME: {new_dur} MIN"})

    if action == "update_time":
        user, mins = data.get('user'), data.get('mins')
        if user in user_processes:
            user_processes[user]['end_time'] = datetime.now() + timedelta(minutes=int(mins))
            return jsonify({"status": "success", "message": f"UPDATED {user} TIME"})
    return jsonify({"status": "error"})

if __name__ == '__main__':
    socketio.run(app, debug=True, port=1000, host='0.0.0.0')
