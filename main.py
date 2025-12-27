import os
import sys
import logging
import io
import subprocess
import json
import time
import struct
import shutil

# Flask imports
try:
    from flask import Flask, request, jsonify, send_file, Blueprint
    from werkzeug.utils import secure_filename
except ImportError as e:
    sys.stderr.write(f"CRITICAL: Missing module '{e.name}'. Install with pip.\n")
    sys.exit(1)

# Scientific imports
try:
    import numpy as np
    import pandas as pd
    # NOTA: Non importiamo pyplot qui per evitare il backend globale stateful
    from matplotlib.figure import Figure 
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    from scipy.signal import butter, filtfilt
except ImportError as e:
    sys.stderr.write(f"CRITICAL: Missing scientific module '{e.name}'.\n")
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

UPLOAD_FOLDER = 'uploads'
DECODER_EXECUTABLE = './fifo_decoder'
# Mapping colonne atteso dal decoder (puoi adattarlo se il tuo decoder usa nomi diversi)
EXPECTED_COLUMNS = ['accX', 'accY', 'accZ', 'gyroX', 'gyroY', 'gyroZ']

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Impostiamo il logger per vedere i messaggi nella console del server
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

api = Blueprint('api', __name__, url_prefix='/api')

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def low_pass_filter(data, cutoff, fs, order=5):
    """
    Butterworth Low Pass Filter.
    Gestisce array vuoti o troppo corti per evitare crash di scipy.
    """
    if len(data) < 15: # Scipy richiede lunghezza > padlen
        return data
        
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    # Validazione parametri filtro
    if normal_cutoff >= 1:
        logging.warning(f"Cutoff {cutoff}Hz is too high for fs {fs}Hz. Adjusting to {nyq*0.9:.1f}Hz")
        normal_cutoff = 0.99
        
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    y = filtfilt(b, a, data)
    return y

def save_uploaded_file(file, session_name, bike_config_str=None):
    """
    Salva file e config gestendo le directory.
    """
    safe_session_name = secure_filename(session_name)
    if not safe_session_name:
        safe_session_name = f"session_{int(time.time())}"

    session_dir = os.path.join(UPLOAD_FOLDER, safe_session_name)
    os.makedirs(session_dir, exist_ok=True)
    
    # Determina estensione sicura
    filename = secure_filename(file.filename)
    file_path = os.path.join(session_dir, filename)
    file.save(file_path)
    logging.info(f"File saved: {file_path}")
    
    bike_config = None
    config_path = None
    
    if bike_config_str:
        try:
            bike_config = json.loads(bike_config_str)
            # Validazione base keys
            required_keys = ['bike', 'fork', 'shock']
            if not all(k in bike_config for k in required_keys):
                logging.warning("Config JSON missing standard keys, proceed anyway.")
            
            config_path = os.path.join(session_dir, 'config.json')
            with open(config_path, 'w') as f:
                json.dump(bike_config, f, indent=2)
                
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON format in bike_config")

    return file_path, config_path, bike_config, session_dir

def process_binary_to_csv(file_path):
    """
    1. Legge il file binario 'misto' (Header Custom + Payload Compresso).
    2. Separa i dati per 'conn_handle' (Sensore).
    3. Salva file temporanei RAW (solo payload compresso).
    4. Chiama il decoder C su ogni file RAW.
    5. Restituisce una lista di path CSV generati.
    """
    if not file_path.lower().endswith('.bin'):
        return [file_path] # Ritorna lista per coerenza
    
    base_dir = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # Dizionario per gestire i file handle dei vari sensori
    # Key: conn_handle, Value: { 'file': file_obj, 'bin_path': str, 'csv_path': str }
    sensor_files = {}
    
    # Struttura Header: conn_handle(2), timestamp(4), data_size(2)
    header_struct = struct.Struct('<HIH') 
    HEADER_SIZE = 8
    
    logging.info(f"Start Demuxing mixed file: {file_path}")
    
    try:
        with open(file_path, 'rb') as f_in:
            while True:
                # 1. Leggi Header
                header_bytes = f_in.read(HEADER_SIZE)
                if len(header_bytes) < HEADER_SIZE:
                    break 
                
                conn_handle, timestamp_ms, data_size = header_struct.unpack(header_bytes)
                
                # 2. Gestione File Temporanei per questo sensore
                if conn_handle not in sensor_files:
                    bin_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.bin")
                    csv_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.csv")
                    sensor_files[conn_handle] = {
                        'file': open(bin_path, 'wb'),
                        'bin_path': bin_path,
                        'csv_path': csv_path
                    }
                    logging.info(f"New sensor detected: {conn_handle}")

                # 3. Leggi Payload Compresso e scrivilo nel file specifico
                payload = f_in.read(data_size)
                if len(payload) < data_size:
                    logging.warning("File truncated unexpectedly")
                    break
                
                # Scriviamo SOLO il payload compresso (senza header custom)
                sensor_files[conn_handle]['file'].write(payload)

    except Exception as e:
        logging.error(f"Demuxing failed: {e}")
        # Chiudi tutto in caso di errore
        for s in sensor_files.values(): s['file'].close()
        raise e
    
    # Chiudi tutti i file binari aperti
    for s in sensor_files.values():
        s['file'].close()
        
    # --- FASE 2: DECODING ---
    generated_csvs = []
    
    # Verifica esistenza decoder
    if not os.path.exists(DECODER_EXECUTABLE):
        raise FileNotFoundError(f"Decoder not found: {DECODER_EXECUTABLE}")
    if not os.access(DECODER_EXECUTABLE, os.X_OK):
        os.chmod(DECODER_EXECUTABLE, 0o755)

    for conn_handle, info in sensor_files.items():
        bin_in = info['bin_path']
        csv_out = info['csv_path']
        
        logging.info(f"Decoding Sensor {conn_handle}: {bin_in} -> {csv_out}")
        
        try:
            # Lancia il decoder C (che ora riceve un file RAW puro compresso ST)
            result = subprocess.run(
                [DECODER_EXECUTABLE, bin_in, csv_out],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                logging.error(f"Decoder failed for sensor {conn_handle}: {result.stderr}")
                continue # Prova con gli altri sensori
            
            if os.path.exists(csv_out) and os.path.getsize(csv_out) > 0:
                generated_csvs.append(csv_out)
            else:
                logging.warning(f"Decoder produced empty CSV for sensor {conn_handle}")

        except subprocess.TimeoutExpired:
            logging.error(f"Decoder timed out for sensor {conn_handle}")

    if not generated_csvs:
        raise RuntimeError("No CSVs were generated successfully from the binary file.")

    return generated_csvs

def analyze_and_plot(csv_paths_list, bike_config=None):
    """
    Genera grafico usando l'approccio Object-Oriented di Matplotlib (Thread-Safe).
    Supporta lista di CSV (multi-sensore).
    """
    # Se arriva un path singolo, lo mettiamo in lista
    if isinstance(csv_paths_list, str):
        csv_paths_list = [csv_paths_list]
        
    # --- CONFIGURAZIONE FISICA E FILTRO ---
    # TODO: Sostituire default con fs ricevuto dall'app (Ora settato a 104Hz)
    fs = 104
    
    # TODO: Modificare cutoff dopo test iniziali. 
    # TODO: Fare test con cutoff compreso tra 10 e 15 Hz. 
    # (Imposto 10Hz che è un buon punto di partenza per 104Hz di campionamento)
    cutoff = 10 
    
    # Fattori di scala (devono corrispondere alla config Firmware)
    ACC_SENSITIVITY = 0.488 / 1000.0  # Converti mg -> g (Per scala 16g)
    GYRO_SENSITIVITY = 70.0 / 1000.0  # Converti mdps -> dps (Per scala 2000dps)

    # --- MATPLOTLIB SETUP ---
    fig = Figure(figsize=(10, 8))
    
    # Titoli dinamici
    title_main = "Telemetry Analysis"
    if bike_config:
        bike = bike_config.get('bike', {})
        title_main = f"{bike.get('bike_type', 'Bike')} - {bike.get('front_wheel_size', '')}\""

    ax1 = fig.add_subplot(2, 1, 1) # Accelerazione
    ax2 = fig.add_subplot(2, 1, 2) # Giroscopio
    
    colors = ['#00A8E8', '#E84A5F', '#FFD460', '#2A363B'] # Colori per sensori diversi
    
    plot_created = False

    for i, csv_path in enumerate(csv_paths_list):
        try:
            # Ottimizzazione: float32 per risparmiare RAM
            df = pd.read_csv(csv_path, dtype='float32')
            
            if df.empty: continue

            # Normalizzazione nomi colonne
            df.columns = [c.strip().lower() for c in df.columns]

            # Separa Accel (Tag 1) e Gyro (Tag 0)
            # NOTA: Assumiamo che il decoder CSV produca colonne: timestamp_ms, tag, x, y, z
            df_acc = df[df['tag'] == 1].copy()
            df_gyro = df[df['tag'] == 0].copy()
            
            sensor_label = f"Sens {i}" # Idealmente useremmo il conn_handle dal nome file

            # --- PLOT ACCELERAZIONE (Z) ---
            if not df_acc.empty:
                # Conversione RAW -> g
                df_acc['accZ_g'] = df_acc['z'] * ACC_SENSITIVITY
                
                # Filtraggio
                clean_z = low_pass_filter(df_acc['accZ_g'].values, cutoff, fs)
                
                ax1.plot(df_acc['timestamp_ms'], df_acc['accZ_g'], 
                         label=f'{sensor_label} Raw Z', alpha=0.3, color='gray', linewidth=0.5)
                ax1.plot(df_acc['timestamp_ms'], clean_z, 
                         label=f'{sensor_label} Filtered Z', color=colors[i % len(colors)], linewidth=1.5)
                plot_created = True

            # --- PLOT GIROSCOPIO (X - Pitch) ---
            if not df_gyro.empty:
                # Conversione RAW -> dps
                df_gyro['gyroX_dps'] = df_gyro['x'] * GYRO_SENSITIVITY
                
                ax2.plot(df_gyro['timestamp_ms'], df_gyro['gyroX_dps'], 
                         label=f'{sensor_label} Pitch Rate', color=colors[i % len(colors)], linewidth=1)
                plot_created = True

        except Exception as e:
            logging.error(f"Error analyzing {csv_path}: {e}")

    if not plot_created:
         ax1.text(0.5, 0.5, "No Valid Data Found", ha='center', va='center')

    # Styling
    ax1.set_title(title_main)
    ax1.set_ylabel('Acceleration [g]')
    ax1.legend(loc='upper right')
    ax1.grid(True, alpha=0.2)

    ax2.set_title('Chassis Rotation (Deg/s)')
    ax2.set_xlabel('Timestamp [ms]') # Ora usiamo ms reali
    ax2.set_ylabel('Angular Velocity [deg/s]')
    ax2.legend(loc='upper right')
    ax2.grid(True, alpha=0.2)

    fig.tight_layout()

    # Rendering su buffer
    img_buf = io.BytesIO()
    FigureCanvas(fig).print_png(img_buf)
    img_buf.seek(0)
    
    return img_buf

# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "online", 
        "service": "SuspensionLab Analytics", 
        "version": "1.6.0" # Bump version
    })

@api.route('/health', methods=['GET'])
def health_check():
    # Verifica spazio disco
    shutil_usage = os.statvfs('.')
    free_space_mb = (shutil_usage.f_bavail * shutil_usage.f_frsize) / 1024 / 1024
    
    return jsonify({
        "status": "healthy",
        "disk_free_mb": int(free_space_mb),
        "decoder_present": os.path.exists(DECODER_EXECUTABLE),
        "api_time": pd.Timestamp.now().isoformat()
    })

@api.route('/upload', methods=['POST'])
def upload_file():
    """Solo upload e salvataggio"""
    try:
        if 'file' not in request.files or 'session_name' not in request.form:
            return jsonify({'error': 'Missing file or session_name'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400

        path, conf_path, conf_data, _ = save_uploaded_file(
            file, 
            request.form['session_name'], 
            request.form.get('bike_config')
        )
        
        return jsonify({
            'status': 'success', 
            'file_path': path,
            'has_config': conf_data is not None
        })

    except Exception as e:
        logging.error(f"Upload Error: {e}")
        return jsonify({'error': str(e)}), 500

@api.route('/upload_and_analyze', methods=['POST'])
def upload_and_analyze():
    """Upload -> Decode (Multi-Sensor) -> Plot"""
    try:
        # 1. Validazione
        if 'file' not in request.files or 'session_name' not in request.form:
            return jsonify({'error': 'Missing file or session_name'}), 400
        
        file = request.files['file']
        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')

        # 2. Salvataggio
        file_path, _, bike_config, _ = save_uploaded_file(file, session_name, bike_config_str)
        
        # 3. Decoding (Restituisce una LISTA di csv, uno per sensore)
        try:
            csv_paths_list = process_binary_to_csv(file_path)
        except Exception as e:
            logging.error(f"Decoding failed: {e}")
            return jsonify({'error': f"Decoder failed: {str(e)}"}), 500
        
        # 4. Plotting (Accetta la lista)
        try:
            img_buf = analyze_and_plot(csv_paths_list, bike_config)
            return send_file(img_buf, mimetype='image/png')
        except Exception as e:
            logging.error(f"Analysis failed: {e}")
            return jsonify({'error': f"Analysis failed: {str(e)}"}), 500

    except Exception as e:
        logging.error(f"System Error: {e}", exc_info=True)
        return jsonify({'error': 'Internal Server Error'}), 500

app.register_blueprint(api)

if __name__ == '__main__':
    # Threaded=True è importante per gestire richieste multiple senza bloccare,
    # anche se su Pi è meglio usare Gunicorn in produzione.
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)