import pandas as pd
import numpy as np
from scipy import signal
import argparse
import json
import sys

def load_and_prep_data(filepath, col_name='acc_z'):
    """
    Carica il CSV e prepara i dati dell'accelerazione verticale.
    """
    try:
        # Tenta di leggere il CSV. Modifica 'sep' se il tuo CSV usa punti e virgola.
        df = pd.read_csv(filepath, sep=',') 
        
        # Pulizia base nomi colonne (toglie spazi extra)
        df.columns = [c.strip().lower() for c in df.columns]
        col_name = col_name.lower()

        if col_name not in df.columns:
            print(f"ERRORE: Colonna '{col_name}' non trovata nel CSV.")
            print(f"Colonne disponibili: {list(df.columns)}")
            sys.exit(1)

        # Rimuove la gravità statica (detrend) per avere oscillazioni attorno a 0
        data = df[col_name].values
        data = data - np.mean(data)
        
        # Calcolo Sampling Rate (Fs) basato sul tempo
        if 'time' in df.columns or 'timestamp' in df.columns:
            t_col = 'time' if 'time' in df.columns else 'timestamp'
            time_vals = df[t_col].values
            duration = time_vals[-1] - time_vals[0]
            fs = len(time_vals) / duration
        else:
            # Fallback se non c'è colonna tempo: assumiamo 100Hz (modificare se noto)
            fs = 100.0
            
        return data, fs

    except Exception as e:
        print(f"Errore nella lettura del file: {e}")
        sys.exit(1)

def analyze_vibrations(data, fs):
    """
    Esegue l'analisi matematica (RMS e PSD).
    """
    # 1. Calcolo RMS (Root Mean Square) - Indice di ruvidità generale
    rms = np.sqrt(np.mean(data**2))

    # 2. Analisi Spettrale (Welch's Method per PSD)
    freqs, psd = signal.welch(data, fs, nperseg=1024)

    # Definizione Bande di Frequenza MTB (Zone di Tuning)
    # Low Speed (Pilot Input/Chassis): 0.5 - 5 Hz
    idx_low = np.logical_and(freqs >= 0.5, freqs < 5)
    power_low = np.trapz(psd[idx_low], freqs[idx_low])

    # Mid Speed (Trail Roughness): 5 - 15 Hz
    idx_mid = np.logical_and(freqs >= 5, freqs < 15)
    power_mid = np.trapz(psd[idx_mid], freqs[idx_mid])

    # High Speed (Harshness/Chatter): 15 - 50 Hz
    idx_high = np.logical_and(freqs >= 15, freqs < 50)
    power_high = np.trapz(psd[idx_high], freqs[idx_high])

    # Trova la frequenza dominante assoluta (dove c'è il picco più alto)
    peak_freq = freqs[np.argmax(psd)]

    return {
        "sampling_rate_hz": round(fs, 1),
        "total_rms_g": round(rms / 9.81, 2), # Convertito in G se input è m/s^2
        "dominant_frequency_hz": round(peak_freq, 1),
        "energy_distribution": {
            "low_freq_zone_0_5hz_chassis": round(power_low, 2),
            "mid_freq_zone_5_15hz_suspension": round(power_mid, 2),
            "high_freq_zone_15_50hz_harshness": round(power_high, 2)
        }
    }

def generate_ai_prompt(analysis, user_notes):
    """
    Formatta l'output in un messaggio pronto per l'AI.
    """
    # Logica di base per interpretazione preliminare
    dominant_zone = max(analysis["energy_distribution"], key=analysis["energy_distribution"].get)
    
    prompt = f"""
--- INIZIO REPORT TELEMETRIA ---
DATI TECNICI CALCOLATI:
- Intensità Vibrazione (RMS): {analysis['total_rms_g']} G
- Frequenza Dominante: {analysis['dominant_frequency_hz']} Hz
- Distribuzione Energia:
  * Low Speed (Corpo/Telaio): {analysis['energy_distribution']['low_freq_zone_0_5hz_chassis']} (Power)
  * Mid Speed (Lavoro Sospensione): {analysis['energy_distribution']['mid_freq_zone_5_15hz_suspension']} (Power)
  * High Speed (Vibrazioni Rapide): {analysis['energy_distribution']['high_freq_zone_15_50hz_harshness']} (Power)

INTERPRETAZIONE PRELIMINARE SCRIPT:
La zona con più energia è: {dominant_zone}. 

NOTE PILOTA:
"{user_notes}"

RICHIESTA PER AI:
Analizza questi dati. La frequenza dominante e la distribuzione dell'energia confermano le sensazioni del pilota? 
Basandoti sui manuali e sulla teoria delle vibrazioni, quali click devo modificare per migliorare il comfort/grip?
--- FINE REPORT ---
"""
    return prompt

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='MTB Telemetry Analyzer for AI')
    parser.add_argument('file', type=str, help='Path al file CSV')
    parser.add_argument('--col', type=str, default='acc_z', help='Nome colonna accelerazione verticale (default: acc_z)')
    parser.add_argument('--note', type=str, default='Nessuna nota specifica', help='Sensazioni del pilota')

    args = parser.parse_args()

    data, fs = load_and_prep_data(args.file, args.col)
    analysis = analyze_vibrations(data, fs)
    final_prompt = generate_ai_prompt(analysis, args.note)

    print("\nCopia tutto il testo qui sotto e incollalo nella tua AI:\n")
    print("="*60)
    print(final_prompt)
    print("="*60)