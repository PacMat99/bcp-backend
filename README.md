# Guida Installazione Server SuspensionLab

## 1. Preparazione Sistema
Installa i compilatori necessari:
```bash
sudo apt update
sudo apt install build-essential libatlas-base-dev python3-dev
```

## 2. Setup Python (Clean Install)
Cancella eventuali cartelle `venv` vecchie e ricrea tutto usando il Python di sistema.

```bash
# Assicurati di essere nella cartella dove hai copiato main.py
rm -rf venv

# Crea venv usando ESPLICITAMENTE python di sistema
/usr/bin/python3 -m venv venv

# Attiva
source venv/bin/activate

# Installa dipendenze
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## 3. Compilazione Decoder C
1. Copia i file `main.c`, `st_fifo.c`, e `st_fifo.h` nella cartella.
2. Compila:
   ```bash
   gcc -o fifo_decoder main.c st_fifo.c
   chmod +x fifo_decoder
   ```

## 4. Avvio Server
```bash
source venv/bin/activate
python3 main.py
```

## 5. Aggiornare il sito dopo una modifica al codice

Se si modifica il Backend (Flask), ogni volta che si cambia il codice .py è necessario riavviare il processo python per leggere il nuovo codice:
```bash
cd ~/bcp-backend/
source venv/bin/activate
python3 main.py
pm2 restart backend-flask
```

## 6. Comandi Utili per Gestire il Server

1. Stato se sono online, quanta CPU/RAM usano e quante volte si sono riavviati: `pm2 status`
2. Log di debug: `pm2 logs`
3. Stop del server: `pm2 stop all`
4. Riavvio del server: `pm2 restart all`
