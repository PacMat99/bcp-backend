import os
import sys
import glob
import time
import argparse
import numpy as np
import pandas as pd
from scipy import signal
import google.generativeai as genai

# --- CONFIGURAZIONE ---
# Incolla qui la tua API KEY oppure impostala come variabile d'ambiente
API_KEY = "INCOLLA_LA_TUA_API_KEY_QUI" 

# Configura Gemini
genai.configure(api_key=API_KEY)

# --- 1. MODULO TELEMETRIA (MATEMATICA) ---
def analyze_telemetry(filepath, col_name='acc_z'):
    """Legge il CSV e calcola FFT e RMS."""
    try:
        print(f"--> Analisi file telemetria: {filepath}...")
        df = pd.read_csv(filepath, sep=',') # Controlla se serve sep=';'
        df.columns = [c.strip().lower() for c in df.columns]
        col_name = col_name.lower()

        if col_name not in df.columns:
            raise ValueError(f"Colonna '{col_name}' non trovata. Colonne: {list(df.columns)}")

        # Detrend e calcolo Frequenza di campionamento
        data = df[col_name].values - np.mean(df[col_name].values)
        
        if 'time' in df.columns:
            duration = df['time'].values[-1] - df['time'].values[0]
            fs = len(df) / duration
        else:
            fs = 100.0 # Fallback default

        # Calcoli Fisici
        rms = np.sqrt(np.mean(data**2))
        freqs, psd = signal.welch(data, fs, nperseg=1024)
        peak_freq = freqs[np.argmax(psd)]
        
        # Integrazione energia per bande
        power_low = np.trapz(psd[(freqs >= 0.5) & (freqs < 5)], freqs[(freqs >= 0.5) & (freqs < 5)])
        power_high = np.trapz(psd[(freqs >= 15) & (freqs < 50)], freqs[(freqs >= 15) & (freqs < 50)])

        return {
            "rms_g": round(rms / 9.81, 2),
            "peak_hz": round(peak_freq, 1),
            "low_energy": round(power_low, 2),
            "high_energy": round(power_high, 2)
        }
    except Exception as e:
        print(f"Errore analisi telemetria: {e}")
        sys.exit(1)

# --- 2. MODULO GESTIONE FILE (KNOWLEDGE BASE) ---
def upload_knowledge_base(folder_path="manuali"):
    """
    Cerca tutti i PDF nella cartella, li carica su Google e restituisce gli oggetti file.
    """
    uploaded_files = []
    pdf_files = glob.glob(os.path.join(folder_path, "*.pdf"))
    
    if not pdf_files:
        print(f"ATTENZIONE: Nessun PDF trovato nella cartella '{folder_path}'. L'AI non avrà manuali.")
        return []

    print(f"--> Caricamento di {len(pdf_files)} manuali su Google AI...")
    
    for pdf in pdf_files:
        print(f"    Caricamento: {os.path.basename(pdf)}...", end=" ")
        try:
            # Carica il file sui server di Google (File API)
            file_ref = genai.upload_file(pdf, mime_type="application/pdf")
            uploaded_files.append(file_ref)
            print("OK.")
        except Exception as e:
            print(f"ERRORE: {e}")
            
    # Attendiamo che i file siano processati (stato ACTIVE)
    print("--> Verifica stato file...")
    for f in uploaded_files:
        while f.state.name == "PROCESSING":
            time.sleep(2)
            f = genai.get_file(f.name)
            
    return uploaded_files

# --- 3. IL CERVELLO (GEMINI AGENT) ---
def run_agent(telemetry_data, user_notes, knowledge_files):
    """
    Configura il modello, il system prompt e genera la risposta.
    """
    
    # SYSTEM PROMPT (La "Bibbia" dell'Agente)
    system_instruction = """
    Sei un Senior MTB Race Engineer. Il tuo compito è ottimizzare le sospensioni basandoti ESCLUSIVAMENTE su:
    1. I manuali tecnici e gli studi scientifici forniti.
    2. I dati di telemetria calcolati (RMS, FFT, Spettro).
    3. Il feedback del pilota.
    
    NON inventare dati. Se il manuale non specifica un click, dillo.
    Usa un approccio logico:
    - Alta energia > 20Hz = Harshness (Problema High Speed Compression o Rebound troppo lento).
    - Alta energia < 5Hz = Instabilità telaio (Problema Low Speed).
    
    Sii conciso, diretto e professionale. Fornisci azioni concrete (es. "Chiudi LSR di 2 click").
    """

    # Creazione del Prompt Utente con i dati processati
    user_prompt = f"""
    ANALISI GIRO:
    - RMS (Fatica fisica): {telemetry_data['rms_g']} G
    - Frequenza Dominante: {telemetry_data['peak_hz']} Hz
    - Energia Basse Freq (Chassis): {telemetry_data['low_energy']}
    - Energia Alte Freq (Harshness): {telemetry_data['high_energy']}
    
    FEEDBACK PILOTA:
    "{user_notes}"
    
    DOMANDA:
    Analizza la correlazione tra il feedback e i dati numerici. 
    Controlla nei manuali allegati le impostazioni per la mia forcella/ammortizzatore e suggerisci le modifiche ai click.
    """

    print("--> Interrogazione Gemini 1.5 Flash (può richiedere qualche secondo)...")
    
    # Configurazione Modello
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_instruction
    )

    # Invio richiesta: Prompt + Lista dei PDF (Knowledge Base)
    # Gemini 1.5 supporta nativamente una lista mista di testo e file
    request_content = [user_prompt] + knowledge_files
    
    response = model.generate_content(request_content)
    
    return response.text

# --- MAIN ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('csv_file', help='Il file CSV della telemetria')
    parser.add_argument('--col', default='acc_z', help='Nome colonna accelerazione')
    parser.add_argument('--note', default='Nessuna nota', help='Il tuo feedback')
    args = parser.parse_args()

    # 1. Analisi Matematica
    telemetry_stats = analyze_telemetry(args.csv_file, args.col)
    
    # 2. Caricamento PDF (Manuali)
    # Assicurati di avere la cartella "manuali" con i PDF dentro
    knowledge_base = upload_knowledge_base("manuali")
    
    # 3. Reasoning AI
    risposta = run_agent(telemetry_stats, args.note, knowledge_base)
    
    print("\n" + "="*50)
    print("REPORT INGEGNERE AI:")
    print("="*50)
    print(risposta)
    
    # Pulizia opzionale: Google cancella i file dopo 48h, 
    # ma potremmo volerli cancellare subito se lo script gira spesso.
    # Per ora li lasciamo gestire alla retention policy automatica.