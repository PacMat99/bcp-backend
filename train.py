import os
import sys
import logging
import io
import subprocess
import json
import time
import struct
from typing import Tuple, List, Dict, Optional

# Flask imports
try:
    from flask import Flask, request, jsonify, send_file, Blueprint
    from werkzeug.utils import secure_filename
except ImportError as e:
    sys.stderr.write(f"CRITICAL: Missing Flask module '{e.name}'. Install with: pip install flask\n")
    sys.exit(1)

# Scientific imports
try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')  # Thread-safe backend
    from matplotlib.figure import Figure 
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    from scipy.signal import butter, filtfilt
except ImportError as e:
    sys.stderr.write(f"CRITICAL: Missing scientific module '{e.name}'. Install with: pip install numpy pandas matplotlib scipy\n")
    sys.exit(1)


# ============================================================================
# CONFIGURATION
# ============================================================================

UPLOAD_FOLDER = 'uploads'
DECODER_EXECUTABLE = './fifo_decoder'

# Costanti sensore LSM6DSOX
ACC_SENSITIVITY_16G = 0.488 / 1000.0  # mg -> g
GYRO_SENSITIVITY_2000DPS = 70.0 / 1000.0  # mdps -> dps

# Filtro passa-basso
DEFAULT_CUTOFF_HZ = 10
DEFAULT_SAMPLE_RATE_HZ = 104
FILTER_ORDER = 5
MIN_SAMPLES_FOR_FILTER = 15

# Protocollo demuxing
DEMUX_HEADER_SIZE = 8
DEMUX_HEADER_FORMAT = '<HIH'  # conn_handle(2), timestamp(4), data_size(2)

# Tag types nel CSV decodificato
TAG_GYRO = 0
TAG_ACC = 1

# Timeout decoder
DECODER_TIMEOUT_SEC = 60


# ============================================================================
# FLASK APP SETUP
# ============================================================================

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

api = Blueprint('api', __name__, url_prefix='/api')


# ============================================================================
# HELPER FUNCTIONS - SIGNAL PROCESSING
# ============================================================================

def low_pass_filter(data: np.ndarray, cutoff: float, fs: float, order: int = FILTER_ORDER) -> np.ndarray:
    """
    Butterworth Low Pass Filter con validazione parametri.
    
    Args:
        data: Array numpy dei dati da filtrare
        cutoff: Frequenza di taglio [Hz]
        fs: Frequenza di campionamento [Hz]
        order: Ordine del filtro
        
    Returns:
        Array numpy filtrato
    """
    if len(data) < MIN_SAMPLES_FOR_FILTER:
        logging.warning(f"Insufficient samples ({len(data)}) for filtering. Returning raw data.")
        return data
        
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    
    if normal_cutoff >= 1.0:
        logging.warning(f"Cutoff {cutoff}Hz exceeds Nyquist for fs={fs}Hz. Clamping to 0.99.")
        normal_cutoff = 0.99
        
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    
    try:
        filtered = filtfilt(b, a, data)
        return filtered
    except Exception as e:
        logging.error(f"Filtering failed: {e}. Returning raw data.")
        return data


def extract_sample_rate(bike_config: Optional[Dict]) -> int:
    """
    Estrae sample rate da bike_config o usa default.
    
    Args:
        bike_config: Dizionario configurazione bici
        
    Returns:
        Sample rate in Hz
    """
    if bike_config and 'hardware' in bike_config:
        sr = bike_config['hardware'].get('sample_rate', DEFAULT_SAMPLE_RATE_HZ)
        logging.info(f"Using sample rate from config: {sr} Hz")
        return sr
    
    logging.info(f"Using default sample rate: {DEFAULT_SAMPLE_RATE_HZ} Hz")
    return DEFAULT_SAMPLE_RATE_HZ


# ============================================================================
# HELPER FUNCTIONS - FILE MANAGEMENT
# ============================================================================

def save_uploaded_file(
    file, 
    session_name: str, 
    bike_config_str: Optional[str] = None, 
    session_config_file = None
) -> Tuple[str, Optional[str], Optional[str], Optional[Dict], str]:
    """
    Salva file telemetria + configurazioni in struttura organizzata.
    
    Args:
        file: File object telemetria (.bin)
        session_name: Nome della sessione
        bike_config_str: JSON string configurazione bici (opzionale)
        session_config_file: File object config sessione (opzionale)
    
    Returns:
        tuple: (telemetry_path, app_config_path, session_config_path, bike_config_dict, session_dir)
        
    Raises:
        ValueError: Se bike_config_str non è JSON valido
    """
    safe_session_name = secure_filename(session_name)
    if not safe_session_name:
        safe_session_name = f"session_{int(time.time())}"
        logging.warning(f"Invalid session_name, using: {safe_session_name}")

    session_dir = os.path.join(UPLOAD_FOLDER, safe_session_name)
    os.makedirs(session_dir, exist_ok=True)
    
    # Salva telemetria
    filename = secure_filename(file.filename) or 'telemetry.bin'
    file_path = os.path.join(session_dir, filename)
    file.save(file_path)
    logging.info(f"📁 Telemetry saved: {file_path} ({os.path.getsize(file_path)} bytes)")
    
    # Salva bike_config (da app Flutter)
    bike_config = None
    app_config_path = None
    
    if bike_config_str:
        try:
            bike_config = json.loads(bike_config_str)
            app_config_path = os.path.join(session_dir, 'bike_config.json')
            with open(app_config_path, 'w') as f:
                json.dump(bike_config, f, indent=2)
            logging.info(f"📝 Bike config saved: {app_config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in bike_config: {str(e)}")
    
    # Salva session_config (parametri registrazione)
    session_config_path = None
    if session_config_file:
        session_config_path = os.path.join(session_dir, 'session_config.json')
        session_config_file.save(session_config_path)
        logging.info(f"📝 Session config saved: {session_config_path}")

    return file_path, app_config_path, session_config_path, bike_config, session_dir


def demux_binary_file(file_path: str) -> Dict[int, str]:
    """
    Demultiplessa file binario multi-sensore in file separati per conn_handle.
    
    Format del pacchetto:
        [conn_handle: uint16][timestamp_ms: uint32][data_size: uint16][payload: bytes]
    
    Args:
        file_path: Path del file .bin multi-sensore
        
    Returns:
        Dict mapping conn_handle -> bin_path
        
    Raises:
        RuntimeError: Se demuxing fallisce completamente
    """
    base_dir = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    sensor_files = {}
    header_struct = struct.Struct(DEMUX_HEADER_FORMAT)
    
    logging.info(f"🔄 Demuxing: {file_path}")
    
    try:
        with open(file_path, 'rb') as f_in:
            packet_count = 0
            
            while True:
                header_bytes = f_in.read(DEMUX_HEADER_SIZE)
                if len(header_bytes) < DEMUX_HEADER_SIZE:
                    break
                
                conn_handle, timestamp_ms, data_size = header_struct.unpack(header_bytes)
                
                # Inizializza file per nuovo sensore
                if conn_handle not in sensor_files:
                    bin_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.bin")
                    sensor_files[conn_handle] = {
                        'file_handle': open(bin_path, 'wb'),
                        'bin_path': bin_path,
                        'packet_count': 0
                    }
                    logging.info(f"  📡 Sensor {conn_handle} detected")

                # Leggi payload
                payload = f_in.read(data_size)
                if len(payload) < data_size:
                    logging.error(f"⚠️  Truncated payload at packet {packet_count}. Expected {data_size}, got {len(payload)}")
                    raise RuntimeError(f"Corrupted binary file: truncated packet at offset {f_in.tell()}")
                
                # Scrivi payload (senza header)
                sensor_files[conn_handle]['file_handle'].write(payload)
                sensor_files[conn_handle]['packet_count'] += 1
                packet_count += 1

    except Exception as e:
        logging.error(f"❌ Demuxing failed: {e}")
        for s in sensor_files.values():
            s['file_handle'].close()
        raise RuntimeError(f"Demuxing error: {str(e)}")
    
    # Chiudi tutti i file
    for conn_handle, info in sensor_files.items():
        info['file_handle'].close()
        logging.info(f"  ✅ Sensor {conn_handle}: {info['packet_count']} packets -> {info['bin_path']}")
    
    if not sensor_files:
        raise RuntimeError("No sensors detected in binary file")
    
    return {ch: info['bin_path'] for ch, info in sensor_files.items()}


def decode_sensor_binary(bin_path: str, csv_path: str) -> bool:
    """
    Decodifica file binario sensore usando decoder C esterno.
    
    Args:
        bin_path: Path input .bin
        csv_path: Path output .csv
        
    Returns:
        True se decodifica successo, False altrimenti
    """
    if not os.path.exists(DECODER_EXECUTABLE):
        raise FileNotFoundError(f"Decoder not found: {DECODER_EXECUTABLE}")
    
    if not os.access(DECODER_EXECUTABLE, os.X_OK):
        os.chmod(DECODER_EXECUTABLE, 0o755)
        logging.info(f"Made decoder executable: {DECODER_EXECUTABLE}")

    try:
        result = subprocess.run(
            [DECODER_EXECUTABLE, bin_path, csv_path],
            capture_output=True,
            text=True,
            timeout=DECODER_TIMEOUT_SEC
        )
        
        if result.returncode != 0:
            logging.error(f"Decoder failed: {result.stderr}")
            return False
        
        if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
            logging.error(f"Decoder produced empty or missing CSV: {csv_path}")
            return False
        
        logging.info(f"  ✅ Decoded: {csv_path} ({os.path.getsize(csv_path)} bytes)")
        return True
        
    except subprocess.TimeoutExpired:
        logging.error(f"⏱️  Decoder timeout ({DECODER_TIMEOUT_SEC}s)")
        return False
    except Exception as e:
        logging.error(f"Decoder exception: {e}")
        return False


def process_binary_to_csv(file_path: str) -> List[str]:
    """
    Pipeline completa: demux + decode.
    
    Args:
        file_path: Path del file telemetria (può essere .bin multi-sensore o .csv singolo)
        
    Returns:
        Lista di path CSV generati (uno per sensore)
        
    Raises:
        RuntimeError: Se nessun CSV viene generato con successo
    """
    # Se già CSV, ritorna direttamente
    if file_path.lower().endswith('.csv'):
        logging.info(f"File already CSV: {file_path}")
        return [file_path]
    
    if not file_path.lower().endswith('.bin'):
        raise ValueError(f"Unsupported file format: {file_path}")
    
    base_dir = os.path.dirname(file_path)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # Step 1: Demux
    sensor_bins = demux_binary_file(file_path)
    
    # Step 2: Decode ogni sensore
    generated_csvs = []
    failed_sensors = []
    
    for conn_handle, bin_path in sensor_bins.items():
        csv_path = os.path.join(base_dir, f"{base_name}_sensor_{conn_handle}.csv")
        
        logging.info(f"🔧 Decoding sensor {conn_handle}")
        
        if decode_sensor_binary(bin_path, csv_path):
            generated_csvs.append(csv_path)
        else:
            failed_sensors.append(conn_handle)
    
    # Fallimento critico se nemmeno un sensore funziona
    if not generated_csvs:
        raise RuntimeError(f"All sensors failed decoding: {failed_sensors}")
    
    if failed_sensors:
        raise RuntimeError(f"Sensors {failed_sensors} failed decoding. Aborting.")
    
    logging.info(f"✅ Successfully decoded {len(generated_csvs)} sensors")
    return generated_csvs


# ============================================================================
# HELPER FUNCTIONS - ANALYSIS & PLOTTING
# ============================================================================

def analyze_and_plot(csv_paths: List[str], bike_config: Optional[Dict] = None) -> io.BytesIO:
    """
    Genera grafico multi-sensore con accelerazione verticale e pitch rate.
    
    Args:
        csv_paths: Lista di path CSV (uno per sensore fisico)
        bike_config: Dizionario configurazione bici
    
    Returns:
        BytesIO buffer contenente PNG
        
    Raises:
        ValueError: Se nessun dato valido trovato
    """
    if isinstance(csv_paths, str):
        csv_paths = [csv_paths]
    
    # Estrai parametri
    sample_rate = extract_sample_rate(bike_config)
    cutoff = DEFAULT_CUTOFF_HZ
    
    # Setup figura
    fig = Figure(figsize=(14, 10))
    fig.patch.set_facecolor('white')
    
    # Titolo dinamico da bike_config
    title_main = "Multi-Sensor Telemetry Analysis"
    subtitle = f"Sample Rate: {sample_rate} Hz | Filter: {cutoff} Hz Low-Pass"
    
    if bike_config:
        bike_type = bike_config.get('type', 'Bike')
        front_tire = bike_config.get('front_tire', {})
        wheel_size = front_tire.get('size', '')
        if wheel_size:
            title_main = f"{bike_type} | {wheel_size}\" Front Wheel"
    
    # Subplot setup
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)
    
    colors = ['#00A8E8', '#E84A5F', '#FFD460', '#2ECC71']
    plot_created = False
    
    # Processa ogni CSV (sensore)
    for sensor_idx, csv_path in enumerate(csv_paths):
        try:
            df = pd.read_csv(csv_path, dtype={'timestamp_ms': 'int64', 'tag': 'int32', 
                                               'x': 'float32', 'y': 'float32', 'z': 'float32'})
            
            if df.empty:
                logging.warning(f"Empty CSV: {csv_path}")
                continue
            
            # Normalizza colonne
            df.columns = [c.strip().lower() for c in df.columns]
            
            # Valida struttura
            required_cols = ['timestamp_ms', 'tag', 'x', 'y', 'z']
            if not all(col in df.columns for col in required_cols):
                logging.error(f"Invalid CSV structure: {csv_path}")
                continue
            
            sensor_label = f"Sensor {sensor_idx + 1}"
            color = colors[sensor_idx % len(colors)]
            
            # Separa per tag
            df_acc = df[df['tag'] == TAG_ACC].copy()
            df_gyro = df[df['tag'] == TAG_GYRO].copy()
            
            # Plot 1: Accelerazione verticale (Z)
            if not df_acc.empty and len(df_acc) > 2:
                df_acc['acc_z_g'] = df_acc['z'] * ACC_SENSITIVITY_16G
                
                time_s = df_acc['timestamp_ms'].values / 1000.0
                acc_raw = df_acc['acc_z_g'].values
                acc_filtered = low_pass_filter(acc_raw, cutoff, sample_rate)
                
                ax1.plot(time_s, acc_raw, label=f'{sensor_label} Raw', 
                        alpha=0.15, color='gray', linewidth=0.5)
                ax1.plot(time_s, acc_filtered, label=f'{sensor_label} Filtered', 
                        color=color, linewidth=1.8)
                plot_created = True
            
            # Plot 2: Pitch rate (Gyro X)
            if not df_gyro.empty and len(df_gyro) > 2:
                df_gyro['gyro_x_dps'] = df_gyro['x'] * GYRO_SENSITIVITY_2000DPS
                
                time_s = df_gyro['timestamp_ms'].values / 1000.0
                gyro_x = df_gyro['gyro_x_dps'].values
                
                ax2.plot(time_s, gyro_x, label=sensor_label, 
                        color=color, linewidth=1.2, alpha=0.8)
                plot_created = True
                
        except Exception as e:
            logging.error(f"Error analyzing {csv_path}: {e}", exc_info=True)
    
    if not plot_created:
        # Fallback se nessun dato valido
        ax1.text(0.5, 0.5, 'NO VALID DATA', ha='center', va='center', 
                fontsize=16, color='red', weight='bold')
        ax2.text(0.5, 0.5, 'NO VALID DATA', ha='center', va='center', 
                fontsize=16, color='red', weight='bold')
    
    # Styling subplot 1
    ax1.set_title(title_main, fontsize=15, fontweight='bold', pad=10)
    ax1.text(0.5, 1.02, subtitle, transform=ax1.transAxes, ha='center', 
            fontsize=9, style='italic', color='gray')
    ax1.set_ylabel('Vertical Acceleration [g]', fontsize=12, fontweight='bold')
    ax1.legend(loc='upper right', fontsize=9, framealpha=0.9)
    ax1.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
    ax1.set_xlim(left=0)
    
    # Styling subplot 2
    ax2.set_title('Pitch Rate (Gyroscope X)', fontsize=13, fontweight='bold')
    ax2.set_xlabel('Time [s]', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Angular Velocity [deg/s]', fontsize=12, fontweight='bold')
    ax2.legend(loc='upper right', fontsize=9, framealpha=0.9)
    ax2.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
    ax2.set_xlim(left=0)
    
    fig.tight_layout()
    
    # Renderizza in buffer
    img_buf = io.BytesIO()
    canvas = FigureCanvas(fig)
    canvas.print_png(img_buf, dpi=100)
    img_buf.seek(0)
    
    return img_buf


# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/', methods=['GET'])
def index():
    """Root endpoint - info server."""
    return jsonify({
        "service": "BCP Analytics Server",
        "status": "online",
        "version": "2.0.0",
        "api_prefix": "/api",
        "endpoints": ["/api/health", "/api/upload", "/api/upload_and_analyze", "/api/analysis/<session_id>"]
    }), 200


@api.route('/health', methods=['GET'])
def health_check():
    """Health check per testConnection() di Flutter."""
    try:
        stat = os.statvfs(UPLOAD_FOLDER)
        free_space_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
        
        return jsonify({
            "status": "healthy",
            "server": "BCP Flask Analytics",
            "version": "2.0.0",
            "timestamp": pd.Timestamp.now().isoformat(),
            "disk_free_mb": int(free_space_mb),
            "decoder_present": os.path.exists(DECODER_EXECUTABLE),
            "upload_folder": UPLOAD_FOLDER
        }), 200
        
    except Exception as e:
        logging.error(f"Health check failed: {e}")
        return jsonify({
            "status": "unhealthy", 
            "error": str(e)
        }), 500


@api.route('/upload', methods=['POST'])
def upload_file():
    """
    Endpoint per uploadFile() di Flutter.
    
    Expected form-data:
        - file: File binario telemetria (required)
        - session_name: Nome sessione (required)
        - bike_config: JSON string configurazione bici (optional)
        - session_config: File JSON config sessione (optional)
    
    Returns:
        JSON con session_id e dettagli salvataggio
    """
    try:
        # Validazione input
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'Missing file in request'}), 400
        
        if 'session_name' not in request.form:
            return jsonify({'status': 'error', 'message': 'Missing session_name in request'}), 400
        
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'status': 'error', 'message': 'Empty or invalid file'}), 400

        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')
        session_config_file = request.files.get('session_config')
        
        # Salvataggio
        file_path, app_conf_path, sess_conf_path, bike_config, session_dir = save_uploaded_file(
            file, session_name, bike_config_str, session_config_file
        )
        
        # Prepara risposta
        response = {
            'status': 'success',
            'session_id': os.path.basename(session_dir),
            'message': 'Upload completed successfully',
            'files': {
                'telemetry': os.path.basename(file_path),
                'bike_config': os.path.basename(app_conf_path) if app_conf_path else None,
                'session_config': os.path.basename(sess_conf_path) if sess_conf_path else None
            }
        }
        
        # Aggiungi info bici se presente
        if bike_config:
            response['bike_info'] = {
                'type': bike_config.get('type'),
                'front_wheel_size': bike_config.get('front_tire', {}).get('size'),
                'sensor_count': bike_config.get('hardware', {}).get('sensor_count'),
                'sample_rate': bike_config.get('hardware', {}).get('sample_rate')
            }
        
        logging.info(f"✅ Upload completed: {session_name}")
        return jsonify(response), 200

    except ValueError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 400
        
    except Exception as e:
        logging.error(f"❌ Upload error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal server error', 'detail': str(e)}), 500


@api.route('/upload_and_analyze', methods=['POST'])
def upload_and_analyze():
    """
    Upload + analisi immediata con grafico.
    
    Expected form-data:
        - file: File binario telemetria (required)
        - session_name: Nome sessione (required)
        - bike_config: JSON string configurazione (optional)
        - session_config: File JSON (optional)
    
    Returns:
        PNG image del grafico
    """
    try:
        # Validazione
        if 'file' not in request.files or 'session_name' not in request.form:
            return jsonify({'error': 'Missing required fields: file and session_name'}), 400
        
        file = request.files['file']
        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')
        session_config_file = request.files.get('session_config')

        # Salvataggio
        file_path, _, _, bike_config, _ = save_uploaded_file(
            file, session_name, bike_config_str, session_config_file
        )
        
        # Processing pipeline
        csv_paths = process_binary_to_csv(file_path)
        
        # Plotting
        img_buf = analyze_and_plot(csv_paths, bike_config)
        
        logging.info(f"✅ Analysis completed: {session_name}")
        return send_file(img_buf, mimetype='image/png', download_name=f'{session_name}_analysis.png')

    except FileNotFoundError as e:
        logging.error(f"File not found: {e}")
        return jsonify({'error': str(e)}), 500
        
    except RuntimeError as e:
        logging.error(f"Processing failed: {e}")
        return jsonify({'error': str(e)}), 500
        
    except ValueError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logging.error(f"❌ Analysis error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error', 'detail': str(e)}), 500


@api.route('/analysis/<session_id>', methods=['GET'])
def get_analysis(session_id: str):
    """
    Recupera metadati e configurazioni di una sessione esistente.
    
    Args:
        session_id: ID della sessione (nome cartella)
    
    Returns:
        JSON con dettagli sessione, file generati e configurazioni
    """
    try:
        safe_session_id = secure_filename(session_id)
        session_dir = os.path.join(UPLOAD_FOLDER, safe_session_id)
        
        if not os.path.exists(session_dir):
            return jsonify({'error': 'Session not found', 'session_id': session_id}), 404
        
        # Lista file
        files = os.listdir(session_dir)
        
        # Carica bike_config se esiste
        bike_config = None
        bike_config_path = os.path.join(session_dir, 'bike_config.json')
        if os.path.exists(bike_config_path):
            with open(bike_config_path, 'r') as f:
                bike_config = json.load(f)
        
        # Carica session_config se esiste
        session_config = None
        session_config_path = os.path.join(session_dir, 'session_config.json')
        if os.path.exists(session_config_path):
            with open(session_config_path, 'r') as f:
                session_config = json.load(f)
        
        # Conta CSV generati (sensori decodificati)
        csv_files = [f for f in files if f.endswith('.csv')]
        bin_files = [f for f in files if f.endswith('.bin')]
        
        response = {
            'session_id': safe_session_id,
            'status': 'completed' if csv_files else 'raw',
            'files': {
                'all': files,
                'csv': csv_files,
                'bin': bin_files
            },
            'sensor_count': len(csv_files),
            'configurations': {
                'bike_config_present': bike_config is not None,
                'session_config_present': session_config is not None,
                'bike_config': bike_config,
                'session_config': session_config
            }
        }
        
        return jsonify(response), 200

    except Exception as e:
        logging.error(f"❌ Get analysis error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error', 'detail': str(e)}), 500


# ============================================================================
# REGISTER BLUEPRINT & RUN
# ============================================================================

app.register_blueprint(api)


if __name__ == '__main__':
    logging.info("=" * 60)
    logging.info("BCP Analytics Server Starting")
    logging.info(f"Upload folder: {os.path.abspath(UPLOAD_FOLDER)}")
    logging.info(f"Decoder: {DECODER_EXECUTABLE} (exists: {os.path.exists(DECODER_EXECUTABLE)})")
    logging.info("=" * 60)
    
    app.run(
        host='0.0.0.0', 
        port=5000, 
        debug=False,  # Set True for development
        threaded=True
    )
