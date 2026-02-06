import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def calculate_fork_travel(file_path, max_travel):
    # Leggi i dati dal file Excel
    data = pd.read_excel(file_path)
    
    # Colonne richieste
    accel_high_x = data['accel_high_x']
    accel_low_x = data['accel_low_x']
    millis = data['millis']
    
    # Calcola il delta t (in secondi)
    time_seconds = millis / 1000.0  # Converti millisecondi in secondi
    dt = np.diff(time_seconds, prepend=time_seconds[0])
    
    # Calcola accelerazione relativa (m/s^2)
    accel_relative = accel_low_x - accel_high_x
    
    # Integra numericamente per ottenere la velocità relativa (m/s)
    velocity_relative = np.zeros_like(accel_relative)
    for i in range(1, len(accel_relative)):
        velocity_relative[i] = velocity_relative[i - 1] + accel_relative[i] * dt[i]
    
    # Integra numericamente per ottenere la posizione relativa (m)
    position_relative = np.zeros_like(velocity_relative)
    for i in range(1, len(velocity_relative)):
        position_relative[i] = position_relative[i - 1] + velocity_relative[i] * dt[i]
    
    # Converti la posizione relativa in mm
    position_relative_mm = position_relative * 1000.0
    
    # Clamping: limita la posizione tra 0 e il travel massimo
    position_relative_mm = np.clip(position_relative_mm, 0, max_travel)
    
    # Aggiungi la posizione relativa al dataframe
    data['fork_travel_mm'] = position_relative_mm
    
    # Genera il grafico
    plt.figure(figsize=(10, 6))
    plt.plot(time_seconds, position_relative_mm, label='Escursione della forcella (mm)', color='blue')
    plt.axhline(0, color='black', linestyle='--', linewidth=0.8)
    plt.axhline(max_travel, color='red', linestyle='--', label=f'Travel massimo ({max_travel} mm)')
    plt.xlabel('Tempo (s)')
    plt.ylabel('Escursione (mm)')
    plt.title('Andamento dell\'escursione della forcella nel tempo')
    plt.legend()
    plt.grid()
    plt.show()
    
    # Ritorna il dataframe con i dati calcolati
    return data

# Esempio di utilizzo
file_path = 'dati_forcella.xlsx'  # Sostituisci con il percorso al tuo file Excel
max_travel = 150.0  # Escursione massima in mm
result_data = calculate_fork_travel(file_path, max_travel)

# Salva i risultati su un nuovo file Excel
result_data.to_excel('dati_calcolati.xlsx', index=False)
