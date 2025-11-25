import os
import sys
import logging
import io
import subprocess

# Dependency Check Block
try:
    from flask import Flask, request, jsonify, send_file
    from flask_cors import CORS
except ImportError as e:
    print(f"\nCRITICAL ERROR: Missing required module '{e.name}'.")
    print("Please update your dependencies by running:")
    print("    pip install -r requirements.txt")
    print("Or install manually: pip install flask flask-cors\n")
    sys.exit(1)

try:
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg') # Non-interactive backend for server
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
DECODER_EXECUTABLE = './fifo_decoder' # The compiled C utility name

app = Flask(__name__)

# Enable CORS specifically for the app domain as requested
# resources={r"/*": ...} applies CORS headers to all routes
CORS(app, resources={r"/*": {"origins": ["https://app.pacsbrothers.com"]}})

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
logging.basicConfig(level=logging.INFO)

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def low_pass_filter(data, cutoff=10, fs=100, order=5):
    """
    Simple Butterworth Low Pass Filter.
    """
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    y = filtfilt(b, a, data)
    return y

# --- ROUTES ---

@app.route('/', methods=['GET'])
def index():
    """
    Root endpoint to verify server is running.
    """
    return jsonify({
        "status": "online", 
        "service": "SuspensionLab Analytics Server", 
        "version": "1.2.0",
        "cors_enabled": True
    }), 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "active", "device": "Raspberry Pi"}), 200

@app.route('/upload_and_analyze', methods=['POST'])
def analyze_telemetry():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    
    # Extract detailed bike configuration from FormData
    bike_type = request.form.get('bike_type', 'Generic')
    wheels = request.form.get('wheels', 'Unknown')
    fork_travel = request.form.get('fork_travel', '')
    shock_travel = request.form.get('shock_travel', '')
    
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        # 1. Decompression / Conversion (if binary)
        if filename.lower().endswith(('.bin', '.dat')):
            csv_filepath = filepath + '.csv'
            logging.info(f"Detected binary file. Attempting to decode using {DECODER_EXECUTABLE}...")
            
            if not os.path.exists(DECODER_EXECUTABLE):
                 return jsonify({"error": "Decoder executable not found on server"}), 500

            # Call the C utility: ./fifo_decoder <input_bin> <output_csv>
            try:
                subprocess.run(
                    [DECODER_EXECUTABLE, filepath, csv_filepath], 
                    check=True, 
                    capture_output=True, 
                    text=True
                )
            except subprocess.CalledProcessError as e:
                logging.error(f"Decoder C failed: {e.stderr}")
                return jsonify({"error": f"Binary decoding failed: {e.stderr}"}), 500
            
            # Switch pointer to the new CSV
            filepath = csv_filepath

        # 2. Read Data
        try:
            df = pd.read_csv(filepath)
        except Exception as e:
             return jsonify({"error": f"Could not read CSV data: {str(e)}"}), 400
        
        # Normalize columns
        cols = [c.lower() for c in df.columns]
        if 'accz' not in cols and len(df.columns) >= 6:
             df.columns = ['accX', 'accY', 'accZ', 'gyroX', 'gyroY', 'gyroZ'][:len(df.columns)]
        elif 'accz' not in cols:
             z_col = next((c for c in df.columns if 'z' in c.lower() and 'acc' in c.lower()), None)
             if z_col:
                 df.rename(columns={z_col: 'accZ'}, inplace=True)

        # 3. Process Data (Filter)
        if 'accZ' in df.columns:
             df['accZ_clean'] = low_pass_filter(df['accZ'].values, cutoff=5, fs=100)
        
        # 4. Generate Plot
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
        
        # Construct Dynamic Title
        title_str = f'Vertical Acceleration - {bike_type} ({wheels}")'
        if fork_travel:
            title_str += f' | Fork: {fork_travel}mm'
        if shock_travel:
            title_str += f' | Shock: {shock_travel}mm'

        # Plot Raw vs Filtered Vertical Acc
        if 'accZ' in df.columns:
            ax1.plot(df.index, df['accZ'], label='Raw Z', alpha=0.3, color='gray')
            ax1.plot(df.index, df.get('accZ_clean', df['accZ']), label='Filtered Z', color='cyan')
            ax1.set_ylabel('Acceleration (g)')
        else:
             ax1.text(0.5, 0.5, "No AccZ Data Found", ha='center')
             
        ax1.set_title(title_str)
        ax1.legend()
        ax1.grid(True, alpha=0.2)
        
        # Plot Gyro Data
        if 'gyroX' in df.columns:
            ax2.plot(df.index, df['gyroX'], label='Pitch', color='orange')
        if 'gyroY' in df.columns:
            ax2.plot(df.index, df['gyroY'], label='Roll', color='purple')
        
        if 'gyroX' not in df.columns and 'gyroY' not in df.columns:
            ax2.text(0.5, 0.5, "No Gyro Data Found", ha='center')
            
        ax2.set_title('Chassis Stability (Gyro)')
        ax2.legend()
        ax2.grid(True, alpha=0.2)
        
        plt.tight_layout()
        
        # 5. Save to Buffer
        img_buf = io.BytesIO()
        plt.savefig(img_buf, format='png', dpi=100)
        img_buf.seek(0)
        plt.close(fig)
        
        return send_file(img_buf, mimetype='image/png')

    except Exception as e:
        logging.error(f"Analysis failed: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)