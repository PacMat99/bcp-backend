import os
import sys
import logging
import io
import subprocess
import json  # ← AGGIUNTO


# Dependency Check Block
try:
    from flask import Flask, request, jsonify, send_file, Blueprint
except ImportError as e:
    print(f"\nCRITICAL ERROR: Missing required module '{e.name}'.")
    print("Please update your dependencies by running:")
    print("    pip install -r requirements.txt")
    print("Or install manually: pip install flask\n")
    sys.exit(1)


try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from werkzeug.utils import secure_filename
    from scipy.signal import butter, filtfilt
except ImportError as e:
    print(f"\nCRITICAL ERROR: Missing scientific module '{e.name}'.")
    print("Please run: pip install -r requirements.txt\n")
    sys.exit(1)


# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv', 'txt', 'bin', 'dat'}
DECODER_EXECUTABLE = './fifo_decoder'


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
logging.basicConfig(level=logging.INFO)


# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# Create API Blueprint with /api prefix
api = Blueprint('api', __name__, url_prefix='/api')


# ============================================================================
# HELPER FUNCTIONS (Condivise tra le route)
# ============================================================================


def low_pass_filter(data, cutoff=10, fs=100, order=5):
    """
    Simple Butterworth Low Pass Filter.
    """
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    y = filtfilt(b, a, data)
    return y


def save_uploaded_file(file, session_name, bike_config_str=None):
    """
    Salva file caricato e configurazione opzionale.
    
    Args:
        file: File object da request.files
        session_name: Nome sessione (verrà sanitizzato)
        bike_config_str: Stringa JSON con configurazione (opzionale)
    
    Returns:
        tuple: (file_path, config_path, bike_config_dict, session_dir)
    
    Raises:
        ValueError: Se JSON è malformato o campi obbligatori mancano
    """
    # Sanitizza session_name per sicurezza
    safe_session_name = secure_filename(session_name)
    
    # Crea directory sessione
    session_dir = os.path.join(UPLOAD_FOLDER, safe_session_name)
    os.makedirs(session_dir, exist_ok=True)
    
    # Salva file con nome standardizzato
    file_extension = os.path.splitext(file.filename)[1] or '.bin'
    file_path = os.path.join(session_dir, f'telemetry{file_extension}')
    file.save(file_path)
    logging.info(f"File saved: {file_path}")
    
    # Salva configurazione se fornita
    bike_config = None
    config_path = None
    
    if bike_config_str:
        try:
            bike_config = json.loads(bike_config_str)
        except json.JSONDecodeError as e:
            raise ValueError(f'Invalid JSON in bike_config: {str(e)}')
        
        # Validazione struttura JSON (campi obbligatori)
        required_keys = ['bike', 'fork', 'shock', 'wheels', 'esp32']
        missing_keys = [key for key in required_keys if key not in bike_config]
        if missing_keys:
            raise ValueError(f'Missing required keys in bike_config: {missing_keys}')
        
        config_path = os.path.join(session_dir, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(bike_config, f, indent=2)
        logging.info(f"Config saved: {config_path}")
    
    return file_path, config_path, bike_config, session_dir


def process_binary_to_csv(file_path):
    """
    Converte file binario in CSV usando decoder C.
    
    Args:
        file_path: Path al file binario (.bin o .dat)
    
    Returns:
        str: Path al file CSV generato
    
    Raises:
        ValueError: Se file non è binario
        FileNotFoundError: Se decoder non esiste
        RuntimeError: Se decoder fallisce
    """
    if not file_path.lower().endswith(('.bin', '.dat')):
        raise ValueError("File is not binary format")
    
    # Genera path CSV
    csv_path = file_path.rsplit('.', 1)[0] + '.csv'
    
    # Verifica che decoder esista
    if not os.path.exists(DECODER_EXECUTABLE):
        raise FileNotFoundError(f"Decoder executable not found: {DECODER_EXECUTABLE}")
    
    logging.info(f"Decoding binary file: {file_path}")
    
    # Esegui decoder con timeout
    try:
        result = subprocess.run(
            [DECODER_EXECUTABLE, file_path, csv_path],
            capture_output=True,
            text=True,
            timeout=60  # Timeout 60 secondi
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Decoder timeout after 60 seconds")
    
    if result.returncode != 0:
        raise RuntimeError(f"Decoder failed: {result.stderr}")
    
    logging.info(f"CSV created: {csv_path}")
    return csv_path


def analyze_and_plot(csv_path, bike_config=None):
    """
    Analizza CSV e genera grafico PNG.
    
    Args:
        csv_path: Path al file CSV con dati telemetria
        bike_config: Dict con configurazione bici (opzionale)
    
    Returns:
        io.BytesIO: Buffer con immagine PNG
    
    Raises:
        ValueError: Se CSV non ha colonne valide
    """
    # Leggi dati
    df = pd.read_csv(csv_path)
    
    if df.empty:
        raise ValueError("CSV file is empty")
    
    # Normalizza colonne
    cols = [c.lower() for c in df.columns]
    if 'accz' not in cols and len(df.columns) >= 6:
        df.columns = ['accX', 'accY', 'accZ', 'gyroX', 'gyroY', 'gyroZ'][:len(df.columns)]
    elif 'accz' not in cols:
        z_col = next((c for c in df.columns if 'z' in c.lower() and 'acc' in c.lower()), None)
        if z_col:
            df.rename(columns={z_col: 'accZ'}, inplace=True)
    
    # Filtra dati accelerometro
    if 'accZ' in df.columns:
        df['accZ_clean'] = low_pass_filter(df['accZ'].values, cutoff=5, fs=100)
    
    # Estrai info bici da config per titolo grafico
    bike_type = 'Unknown'
    wheels = 'Unknown'
    fork_travel = ''
    shock_travel = ''
    
    if bike_config:
        bike_type = bike_config.get('bike', {}).get('bike_type', 'Unknown')
        wheels = str(bike_config.get('bike', {}).get('front_wheel_size', 'Unknown'))
        fork_travel = str(bike_config.get('fork', {}).get('travel_mm', ''))
        shock_travel = str(bike_config.get('shock', {}).get('travel_mm', ''))
    
    # Genera grafico
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    # Titolo dinamico
    title_str = f'Vertical Acceleration - {bike_type} ({wheels}")'
    if fork_travel:
        title_str += f' | Fork: {fork_travel}mm'
    if shock_travel:
        title_str += f' | Shock: {shock_travel}mm'
    
    # Plot Accelerazione Verticale
    if 'accZ' in df.columns:
        ax1.plot(df.index, df['accZ'], label='Raw Z', alpha=0.3, color='gray')
        ax1.plot(df.index, df.get('accZ_clean', df['accZ']), label='Filtered Z', color='cyan')
        ax1.set_ylabel('Acceleration (g)')
    else:
        ax1.text(0.5, 0.5, "No AccZ Data Found", ha='center', va='center')
    
    ax1.set_title(title_str)
    ax1.legend()
    ax1.grid(True, alpha=0.2)
    
    # Plot Giroscopio (Pitch & Roll)
    if 'gyroX' in df.columns:
        ax2.plot(df.index, df['gyroX'], label='Pitch', color='orange')
    if 'gyroY' in df.columns:
        ax2.plot(df.index, df['gyroY'], label='Roll', color='purple')
    
    if 'gyroX' not in df.columns and 'gyroY' not in df.columns:
        ax2.text(0.5, 0.5, "No Gyro Data Found", ha='center', va='center')
    
    ax2.set_title('Chassis Stability (Gyro)')
    ax2.set_xlabel('Sample Index')
    ax2.set_ylabel('Angular Velocity (deg/s)')
    ax2.legend()
    ax2.grid(True, alpha=0.2)
    
    plt.tight_layout()
    
    # Salva in buffer
    img_buf = io.BytesIO()
    plt.savefig(img_buf, format='png', dpi=100)
    img_buf.seek(0)
    plt.close(fig)
    
    return img_buf


# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/', methods=['GET'])
def index():
    """
    Root endpoint to verify server is running.
    """
    return jsonify({
        "status": "online", 
        "service": "SuspensionLab Analytics Server", 
        "version": "1.4.0",
        "api_endpoint": "api.pacsbrothers.com",
        "client": "Flutter Mobile App"
    }), 200


@api.route('/health', methods=['GET'])
def health_check():
    """
    Enhanced health check endpoint for Flutter app connectivity testing.
    Returns detailed server status and capabilities.
    """
    return jsonify({
        "status": "healthy",
        "server": "Flask on Raspberry Pi",
        "api_version": "1.4.0",
        "endpoints_available": [
            "/api/health",
            "/api/upload",
            "/api/upload_and_analyze"
        ],
        "tunnel": "cloudflared",
        "timestamp": pd.Timestamp.now().isoformat()
    }), 200


@api.route('/upload', methods=['POST'])
def upload_file():
    """
    Upload file + config, SENZA elaborazione immediata.
    
    Uso: Salvataggio veloce durante la guida per elaborazione successiva.
    
    Richiede (multipart/form-data):
        - file: File binario (.bin, .dat, .csv, ecc.)
        - session_name: Nome/timestamp sessione
        - bike_config: Stringa JSON con configurazione completa (opzionale)
    
    Restituisce:
        JSON con session_id e path file salvati
    """
    try:
        # Validazione campi obbligatori
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        if 'session_name' not in request.form:
            return jsonify({'status': 'error', 'message': 'No session_name provided'}), 400
        
        file = request.files['file']
        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')  # Opzionale
        
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'Empty filename'}), 400
        
        # Salva file e config usando helper function
        file_path, config_path, bike_config, session_dir = save_uploaded_file(
            file, session_name, bike_config_str
        )
        
        # Risposta di successo
        response = {
            'status': 'success',
            'session_id': os.path.basename(session_dir),
            'message': 'File uploaded successfully',
            'files': {
                'telemetry': file_path,
                'config': config_path
            }
        }
        
        # Aggiungi info bici se config presente
        if bike_config:
            response['bike_info'] = {
                'type': bike_config.get('bike', {}).get('bike_type'),
                'fork_travel': bike_config.get('fork', {}).get('travel_mm'),
                'shock_travel': bike_config.get('shock', {}).get('travel_mm'),
                'sensor_count': bike_config.get('esp32', {}).get('sensor_count')
            }
        
        return jsonify(response), 200
        
    except ValueError as e:
        # Errori di validazione (JSON malformato, campi mancanti)
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        # Errori interni del server
        logging.error(f"Upload failed: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'Internal server error: {str(e)}'}), 500


@api.route('/upload_and_analyze', methods=['POST'])
def upload_and_analyze():
    """
    Upload file + config + elaborazione IMMEDIATA con grafico.
    
    Uso: Visualizzazione istantanea del grafico nell'app dopo la guida.
    
    Richiede (multipart/form-data):
        - file: File binario (.bin, .dat) o CSV
        - session_name: Nome/timestamp sessione
        - bike_config: Stringa JSON con configurazione completa (opzionale)
    
    Restituisce:
        Immagine PNG con grafico analisi
    """
    try:
        # Validazione campi obbligatori
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        if 'session_name' not in request.form:
            return jsonify({'error': 'No session_name provided'}), 400
        
        file = request.files['file']
        session_name = request.form['session_name']
        bike_config_str = request.form.get('bike_config')
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename'}), 400
        
        # 1. Salva file e config
        file_path, config_path, bike_config, session_dir = save_uploaded_file(
            file, session_name, bike_config_str
        )
        
        # 2. Converti binario in CSV se necessario
        csv_path = file_path
        if file_path.lower().endswith(('.bin', '.dat')):
            csv_path = process_binary_to_csv(file_path)
        
        # 3. Analizza e genera grafico
        img_buf = analyze_and_plot(csv_path, bike_config)
        
        # 4. Restituisci immagine PNG
        return send_file(img_buf, mimetype='image/png')
        
    except ValueError as e:
        # Errori di validazione
        return jsonify({'error': str(e)}), 400
    except FileNotFoundError as e:
        # Decoder non trovato
        return jsonify({'error': str(e)}), 500
    except RuntimeError as e:
        # Decoder fallito o timeout
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        # Errori generici
        logging.error(f"Analysis failed: {e}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


# Register the API blueprint
app.register_blueprint(api)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
