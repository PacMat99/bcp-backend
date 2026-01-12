import os
import sys
import logging
import io
import subprocess
import json
import time
import struct


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
EXPECTED_COLUMNS = ['accX', 'accY', 'accZ', 'gyroX', 'gyroY', 'gyroZ']

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
api = Blueprint('api', __name__, url_prefix='/api')


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def low_pass_filter(data, cutoff, fs, order=5):
    """Butterworth Low Pass Filter con validazione."""
    if len(data) < 15:
        return data
        
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    
    if normal_cutoff >= 1:
        logging.warning(f"Cutoff {cutoff}Hz too high for fs {fs}Hz. Adjusting.")
        normal_cutoff = 0.99
        
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    y = filtfilt(b, a, data)
    return y


def save_uploaded_file(file, session_name, bike_config_str=None, session_config_file=None):
    """
    Salva file telemetria + configurazioni.
    
    Args:
        file: File binario telemetria
        session_name: Nome sessione
        bike_config_str: JSON string config app (opzionale)
        session_config_file: File config.json specifico sessione (opzionale)
    
    Returns:
        tuple: (file_path, app_config_path, session_config_path, bike_config_dict, session_dir)
    """
    safe_session_name = secure_filename(session_name)
    if not safe_session_name:
        safe_session_name = f"session_{int(time.time())}"

    session_dir = os.path.join(UPLOAD_FOLDER, safe_session_name)
    os.makedirs(session_dir, exist_ok=True)
    
    # Salva file telemetria
    filename = secure_filename(file.filename)
    file_path = os.path.join(session_dir, filename)
    file.save(file_path)
    logging.info(f"📁 Telemetry file saved: {file_path}")
    
    # Salva bike_config (da app)
    bike_config = None
    app_config_path = None
    
    if bike_config_str:
        try:
            bike_config = json.loads(bike_config_str)
            app_config_path = os.path.join(session_dir, 'app_config.json')
            with open(app_config_path, 'w') as f:
                json.dump(bike_config, f, indent=2)
            logging.info(f"📝 App config saved: {app_config_path}")
        except json.JSONDecodeError as e:
            logging.error(f"Invalid bike_config JSON: {e}")
            raise ValueError(f"Invalid JSON in bike_config: {str(e)}")
    
    # Salva session_config (specifico della registrazione)
    session_config_path = None
    if session_config_file:
        session_config_path = os.path.join(session_dir, 'session_config.json')
        session_config_file.save(session_config_path)
        logging.info(f"📝 Session config saved: {session_config_path}")

    return file_path, app_config_path, session_config_path, bike_config, session_dir


def process_binary_to_csv(file_path):
    """
    Demux file binario multi-sensore e decodifica.
    
    Returns:
        list: Lista di path CSV generati (uno per sensore)
    """
    if not file_path.lower().endswith('.bin'):
        return [file_path]
    
    base_dir = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    sensor_files = {}
    header_struct = struct.Struct('<HIH')  # conn_handle, timestamp, data_size
    HEADER_SIZE = 8
    
    logging.info(f"🔄 Demuxing: {file_path}")
    
    try:
        with open(file_path, 'rb') as f_in:
            while True:
                header_bytes = f_in.read(HEADER_SIZE)
                if len(header_bytes) < HEADER_SIZE:
                    break
                
                conn_handle, timestamp_ms, data_size = header_struct.unpack(header_bytes)
                
                if conn_handle not in sensor_files:
                    bin_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.bin")
                    csv_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.csv")
                    sensor_files[conn_handle] = {
                        'file': open(bin_path, 'wb'),
                        'bin_path': bin_path,
                        'csv_path': csv_path
                    }
                    logging.info(f"  📡 Sensor {conn_handle} detected")

                payload = f_in.read(data_size)
                if len(payload) < data_size:
                    logging.warning("⚠️  File truncated")
                    break
                
                sensor_files[conn_handle]['file'].write(payload)

    except Exception as e:
        logging.error(f"❌ Demuxing failed: {e}")
        for s in sensor_files.values():
            s['file'].close()
        raise
    
    for s in sensor_files.values():
        s['file'].close()
        
    # Decoding
    generated_csvs = []
    
    if not os.path.exists(DECODER_EXECUTABLE):
        raise FileNotFoundError(f"Decoder not found: {DECODER_EXECUTABLE}")
    if not os.access(DECODER_EXECUTABLE, os.X_OK):
        os.chmod(DECODER_EXECUTABLE, 0o755)

    for conn_handle, info in sensor_files.items():
        bin_in = info['bin_path']
        csv_out = info['csv_path']
        
        logging.info(f"🔧 Decoding sensor {conn_handle}")
        
        try:
            result = subprocess.run(
                [DECODER_EXECUTABLE, bin_in, csv_out],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                logging.error(f"❌ Decoder failed for sensor {conn_handle}: {result.stderr}")
                continue
            
            if os.path.exists(csv_out) and os.path.getsize(csv_out) > 0:
                generated_csvs.append(csv_out)
                logging.info(f"  ✅ CSV generated: {csv_out}")
            else:
                logging.warning(f"  ⚠️  Empty CSV for sensor {conn_handle}")

        except subprocess.TimeoutExpired:
            logging.error(f"⏱️  Decoder timeout for sensor {conn_handle}")

    if not generated_csvs:
        raise RuntimeError("No CSVs generated successfully")

    return generated_csvs


def analyze_and_plot(csv_paths_list, bike_config=None):
    """
    Genera grafico multi-sensore thread-safe.
    
    Args:
        csv_paths_list: Lista di path CSV (uno per sensore)
        bike_config: Dict configurazione bici (opzionale)
    
    Returns:
        io.BytesIO: Buffer con PNG
    """
    if isinstance(csv_paths_list, str):
        csv_paths_list = [csv_paths_list]
        
    # Configurazione
    fs = 104  # Hz
    cutoff = 10  # Hz
    ACC_SENSITIVITY = 0.488 / 1000.0  # mg -> g (scala 16g)
    GYRO_SENSITIVITY = 70.0 / 1000.0  # mdps -> dps (scala 2000dps)

    fig = Figure(figsize=(12, 8))
    
    # Titolo dinamico
    title_main = "Multi-Sensor Telemetry Analysis"
    if bike_config:
        bike = bike_config.get('bike', {})
        bike_type = bike.get('bike_type', 'Bike')
        wheel_size = bike.get('front_wheel_size', '')
        title_main = f"{bike_type} - {wheel_size}\""

    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)
    
    colors = ['#00A8E8', '#E84A5F', '#FFD460', '#2ECC71']
    plot_created = False

    for i, csv_path in enumerate(csv_paths_list):
        try:
            df = pd.read_csv(csv_path, dtype='float32')
            if df.empty:
                continue

            df.columns = [c.strip().lower() for c in df.columns]

            # Separa per tag
            df_acc = df[df['tag'] == 1].copy()
            df_gyro = df[df['tag'] == 0].copy()
            
            sensor_label = f"Sensor {i+1}"

            # Plot Accelerazione Z
            if not df_acc.empty:
                df_acc['accZ_g'] = df_acc['z'] * ACC_SENSITIVITY
                clean_z = low_pass_filter(df_acc['accZ_g'].values, cutoff, fs)
                
                ax1.plot(df_acc['timestamp_ms'], df_acc['accZ_g'], 
                         label=f'{sensor_label} Raw', alpha=0.2, color='gray', linewidth=0.5)
                ax1.plot(df_acc['timestamp_ms'], clean_z, 
                         label=f'{sensor_label} Filtered', color=colors[i % len(colors)], linewidth=1.5)
                plot_created = True

            # Plot Giroscopio X (Pitch)
            if not df_gyro.empty:
                df_gyro['gyroX_dps'] = df_gyro['x'] * GYRO_SENSITIVITY
                ax2.plot(df_gyro['timestamp_ms'], df_gyro['gyroX_dps'], 
                         label=f'{sensor_label}', color=colors[i % len(colors)], linewidth=1)
                plot_created = True

        except Exception as e:
            logging.error(f"Error analyzing {csv_path}: {e}")

    if not plot_created:
        ax1.text(0.5, 0.5, "No Valid Data", ha='center', va='center', fontsize=14)

    # Styling
    ax1.set_title(title_main, fontsize=14, fontweight='bold')
    ax1.set_ylabel('Vertical Acceleration [g]', fontsize=11)
    ax1.legend(loc='upper right', fontsize=9)
    ax1.grid(True, alpha=0.3, linestyle='--')

    ax2.set_title('Pitch Rate', fontsize=12)
    ax2.set_xlabel('Time [ms]', fontsize=11)
    ax2.set_ylabel('Angular Velocity [deg/s]', fontsize=11)
    ax2.legend(loc='upper right', fontsize=9)
    ax2.grid(True, alpha=0.3, linestyle='--')

    fig.tight_layout()

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
        "version": "2.0.0",
        "api_endpoint": "api.pacsbrothers.com"
    })


@api.route('/health', methods=['GET'])
def health_check():
    """Health check per testConnection() di Flutter."""
    try:
        # Verifica spazio disco
        stat = os.statvfs(UPLOAD_FOLDER)
        free_space_mb = (stat.f_bavail * stat.f_frsize) / 1024 / 1024
        
        return jsonify({
            "status": "healthy",
            "server": "Flask on Raspberry Pi",
            "api_version": "2.0.0",
            "disk_free_mb": int(free_space_mb),
            "decoder_present": os.path.exists(DECODER_EXECUTABLE),
            "timestamp": pd.Timestamp.now().isoformat()
        }), 200
    except Exception as e:
        logging.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@api.route('/upload', methods=['POST'])
def upload_file():
    """
    Endpoint per uploadFile() di Flutter.
    Gestisce file telemetria + bike_config (app) + session_config (registrazione).
    """
    try:
        # Validazione
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        if 'session_name' not in request.form:
            return jsonify({'status': 'error', 'message': 'No session_name provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'Empty filename'}), 400

        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')  # Opzionale
        session_config_file = request.files.get('session_config')  # Opzionale
        
        # Salvataggio
        file_path, app_conf_path, sess_conf_path, bike_config, session_dir = save_uploaded_file(
            file, 
            session_name, 
            bike_config_str,
            session_config_file
        )
        
        response = {
            'status': 'success',
            'session_id': os.path.basename(session_dir),
            'message': 'Upload completed successfully',
            'files': {
                'telemetry': os.path.basename(file_path),
                'app_config': os.path.basename(app_conf_path) if app_conf_path else None,
                'session_config': os.path.basename(sess_conf_path) if sess_conf_path else None
            }
        }
        
        if bike_config:
            response['bike_info'] = {
                'type': bike_config.get('bike', {}).get('bike_type'),
                'wheel_size': bike_config.get('bike', {}).get('front_wheel_size')
            }
        
        logging.info(f"✅ Upload completed: {session_name}")
        return jsonify(response), 200

    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        logging.error(f"❌ Upload error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500


@api.route('/upload_and_analyze', methods=['POST'])
def upload_and_analyze():
    """Upload + analisi immediata con grafico."""
    try:
        if 'file' not in request.files or 'session_name' not in request.form:
            return jsonify({'error': 'Missing file or session_name'}), 400
        
        file = request.files['file']
        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')
        session_config_file = request.files.get('session_config')

        # Salvataggio
        file_path, _, _, bike_config, _ = save_uploaded_file(
            file, session_name, bike_config_str, session_config_file
        )
        
        # Decoding
        csv_paths = process_binary_to_csv(file_path)
        
        # Plotting
        img_buf = analyze_and_plot(csv_paths, bike_config)
        
        logging.info(f"✅ Analysis completed: {session_name}")
        return send_file(img_buf, mimetype='image/png')

    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 500
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        logging.error(f"❌ Analysis error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@api.route('/analysis/<session_id>', methods=['GET'])
def get_analysis(session_id):
    """
    Endpoint per getAnalysis() di Flutter.
    Restituisce metadati e info sulla sessione.
    """
    try:
        safe_session_id = secure_filename(session_id)
        session_dir = os.path.join(UPLOAD_FOLDER, safe_session_id)
        
        if not os.path.exists(session_dir):
            return jsonify({'error': 'Session not found'}), 404
        
        # Raccogli info
        files = os.listdir(session_dir)
        
        app_config = None
        app_config_path = os.path.join(session_dir, 'app_config.json')
        if os.path.exists(app_config_path):
            with open(app_config_path, 'r') as f:
                app_config = json.load(f)
        
        session_config = None
        session_config_path = os.path.join(session_dir, 'session_config.json')
        if os.path.exists(session_config_path):
            with open(session_config_path, 'r') as f:
                session_config = json.load(f)
        
        # Conta CSV generati (sensori)
        csv_files = [f for f in files if f.endswith('.csv')]
        
        response = {
            'session_id': safe_session_id,
            'status': 'completed',
            'files': files,
            'sensor_count': len(csv_files),
            'has_app_config': app_config is not None,
            'has_session_config': session_config is not None,
            'app_config': app_config,
            'session_config': session_config
        }
        
        return jsonify(response), 200

    except Exception as e:
        logging.error(f"❌ Get analysis error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

app.register_blueprint(api)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)