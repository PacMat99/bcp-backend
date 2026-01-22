import os
import struct
import subprocess
import pandas as pd
import matplotlib.pyplot as plt

# ================= CONFIGURAZIONE =================
INPUT_FILE = 'R001.BIN'       # Il tuo file scaricato dalla SD
DECODER_EXE = './st_fifo.run' # Il tuo eseguibile C
OUTPUT_DIR = 'test_output'     # Dove salvare i risultati
# ==================================================

def run_test():
    print(f"--- AVVIO TEST PIPELINE SU {INPUT_FILE} ---")
    
    if not os.path.exists(INPUT_FILE):
        print(f"ERRORE: File {INPUT_FILE} non trovato!")
        return

    if not os.path.exists(DECODER_EXE):
        print(f"ERRORE: Decoder {DECODER_EXE} non trovato!")
        return

    # Assicura permessi di esecuzione al decoder
    if not os.access(DECODER_EXE, os.X_OK):
        os.chmod(DECODER_EXE, 0o755)
        print("Permessi di esecuzione aggiunti al decoder.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # ---------------------------------------------------------
    # FASE 1: DEMUXING (Separazione flussi sensori)
    # ---------------------------------------------------------
    print("\n[FASE 1] Demuxing del file binario...")
    
    # Header Struct: conn_handle(2) + timestamp(4) + data_size(2) = 8 bytes
    header_struct = struct.Struct('<HIH') 
    HEADER_SIZE = 8
    
    sensor_files = {} # Dizionario per tenere aperti i file temporanei
    packet_count = 0
    
    try:
        with open(INPUT_FILE, 'rb') as f_in:
            while True:
                # Leggi Header
                header_bytes = f_in.read(HEADER_SIZE)
                if len(header_bytes) < HEADER_SIZE:
                    break # Fine file
                
                conn_handle, timestamp_ms, data_size = header_struct.unpack(header_bytes)
                
                # Leggi Payload (Dati compressi ST)
                payload = f_in.read(data_size)
                if len(payload) < data_size:
                    print(f"⚠️ Warning: File tronco al pacchetto {packet_count}")
                    break
                
                # Apri il file specifico per questo sensore se non esiste
                if conn_handle not in sensor_files:
                    bin_path = os.path.join(OUTPUT_DIR, f"sensor_{conn_handle}.bin")
                    csv_path = os.path.join(OUTPUT_DIR, f"sensor_{conn_handle}.csv")
                    sensor_files[conn_handle] = {
                        'f_obj': open(bin_path, 'wb'), # Apre in write binary
                        'bin_path': bin_path,
                        'csv_path': csv_path,
                        'packets': 0
                    }
                    print(f"  -> Trovato nuovo sensore ID: {conn_handle}")
                
                # Scrivi SOLO il payload raw (senza header custom) nel file temporaneo
                sensor_files[conn_handle]['f_obj'].write(payload)
                sensor_files[conn_handle]['packets'] += 1
                packet_count += 1
                
    except Exception as e:
        print(f"❌ Errore durante il demuxing: {e}")
        return
    finally:
        # Chiudi tutti i file aperti
        for info in sensor_files.values():
            info['f_obj'].close()
            
    print(f"  -> Totale pacchetti processati: {packet_count}")
    print(f"  -> File temporanei creati in: {OUTPUT_DIR}/")

    # ---------------------------------------------------------
    # FASE 2: DECODING (Esecuzione Decoder C)
    # ---------------------------------------------------------
    print("\n[FASE 2] Decoding con st_fifo.run...")
    
    decoded_files = []
    
    for handle, info in sensor_files.items():
        bin_in = info['bin_path']
        csv_out = info['csv_path']
        
        print(f"  -> Decodifica sensore {handle} ({info['packets']} pacchetti)...")
        
        try:
            # Esegue il comando: ./st_fifo.run [input] [output]
            result = subprocess.run(
                [DECODER_EXE, bin_in, csv_out],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Verifica che il CSV non sia vuoto
                if os.path.exists(csv_out) and os.path.getsize(csv_out) > 0:
                    print(f"     ✅ Successo! Generato: {csv_out}")
                    decoded_files.append(csv_out)
                else:
                    print(f"     ⚠️ Decoder terminato ma CSV vuoto/assente.")
            else:
                print(f"     ❌ Errore Decoder: {result.stderr}")
                
        except Exception as e:
            print(f"     ❌ Errore esecuzione subprocess: {e}")

    # ---------------------------------------------------------
    # FASE 3: PLOTTING (Verifica Visiva)
    # ---------------------------------------------------------
    if not decoded_files:
        print("\n❌ Nessun file CSV valido generato. Test fallito.")
        return

    print("\n[FASE 3] Generazione Grafico di Test...")
    
    plt.figure(figsize=(12, 6))
    
    for csv_file in decoded_files:
        try:
            # Leggi il CSV
            df = pd.read_csv(csv_file)
            
            # Cerca colonne accelerometro (flessibilità sui nomi)
            # Il decoder ST di solito produce colonne tipo "AccX [mg]", "AccY [mg]"...
            # O "X", "Y", "Z". Adattiamo la ricerca.
            cols = [c.lower() for c in df.columns]
            
            # Cerchiamo l'indice della colonna Z
            z_col_name = None
            for col in df.columns:
                if 'acc' in col.lower() and 'z' in col.lower():
                    z_col_name = col
                    break
            
            # Se non trova nomi espliciti, prova la 3a colonna (indice 2) se esiste
            if not z_col_name and len(df.columns) >= 3:
                z_col_name = df.columns[2]
            
            if z_col_name:
                plt.plot(df.index, df[z_col_name], label=os.path.basename(csv_file), alpha=0.7)
            else:
                print(f"     ⚠️ Impossibile trovare colonna AccZ in {csv_file}")
                
        except Exception as e:
            print(f"     ❌ Errore lettura CSV {csv_file}: {e}")
            
    plt.title("Test Lettura Dati compressi (LSM6DSOX)")
    plt.xlabel("Campioni")
    plt.ylabel("Valore Raw (mg o LSB)")
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    output_img = os.path.join(OUTPUT_DIR, 'test_plot.png')
    plt.savefig(output_img)
    print(f"\n✅ TEST COMPLETATO! Grafico salvato in: {output_img}")

if __name__ == "__main__":
    run_test()