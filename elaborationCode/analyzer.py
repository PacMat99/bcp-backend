__author__ = "Mattia Pacchin"

import re
import statistics
import matplotlib.pyplot as plt
import numpy as np

'''
0x6A IMU CONFIGURATION (the higher one)
ACCEL final zero rate offset in m/s^2:
- Accel X offset: -0.0762
- Accel Y offset: -0.2324
- Accel Z offset: 0.4344
GYRO final zero rate offset in radians/s:
- Gyro X offset: 0.0134
- Gyro Y offset: -0.0046
- Gyro Z offset: -0.0137
'''
accelHighXOffset = -0.0762  # Calibration offset for accelerometer X
accelHighYOffset = -0.2324  # Calibration offset for accelerometer Y
accelHighZOffset = 0.4344   # Calibration offset for accelerometer Z
gyroHighXOffset = 0.0134    # Calibration offset for gyroscope X
gyroHighYOffset = -0.0046   # Calibration offset for gyroscope Y
gyroHighZOffset = -0.0137   # Calibration offset for gyroscope Z

'''
0x6B IMU CONFIGURATION (the lower one)
ACCEL final zero rate offset in m/s^2:
- Accel X range: -0.0890
- Accel Y range: -0.2155
- Accel Z range: 0.3289
GYRO final zero rate offset in radians/s:
- Gyro X range: 0.0005
- Gyro Y range: 0.0027
- Gyro Z range: -0.0092
'''
accelLowXOffset = -0.0890  # Calibration offset for accelerometer X
accelLowYOffset = -0.2155  # Calibration offset for accelerometer Y
accelLowZOffset = 0.3289   # Calibration offset for accelerometer Z
gyroLowXOffset = 0.0005    # Calibration offset for gyroscope X
gyroLowYOffset = 0.0027    # Calibration offset for gyroscope Y
gyroLowZOffset = -0.0092   # Calibration offset for gyroscope Z

def read_file(f_name):
    f_array = np.genfromtxt(f_name, delimiter=";")
    return f_array

def get_travel():
    # Dati simulati
    acceleration = np.array([...])  # Accelerazione lungo l'asse travel
    time = np.array([...])          # Tempo dei campionamenti

    # Calcolo delta t
    delta_t = np.diff(time)

    # Integrazione per ottenere la velocità
    velocity = np.cumsum(acceleration[:-1] * delta_t)

    # Integrazione per ottenere la posizione (travel)
    position = np.cumsum(velocity * delta_t)

    # Travel massimo
    travel_max = np.max(np.abs(position))

    print(f"Travel massimo della forcella: {travel_max} m")

'''
def lines_to_dict(file_lines):
    count = 0
    reset_time = 0
    dict = {}
    for l in file_lines:
        if count == 0:
            keys = re.split(";", l)
            for k in keys:
                dict[k] = []
            count += 1
        else:
            values = re.split(";", l)
            if count == 1:
                reset_time = int(values[14])
                count += 1
            dict["temp_1"].append(float(values[0]))
            dict["accel_1_x"].append(float(values[1]))
            dict["accel_1_y"].append(float(values[2]))
            dict["accel_1_z"].append(float(values[3]))
            dict["gyro_1_x"].append(float(values[4]))
            dict["gyro_1_y"].append(float(values[5]))
            dict["gyro_1_z"].append(float(values[6]))
            dict["temp_2"].append(float(values[7]))
            dict["accel_2_x"].append(float(values[8]))
            dict["accel_2_y"].append(float(values[9]))
            dict["accel_2_z"].append(float(values[10]))
            dict["gyro_2_x"].append(float(values[11]))
            dict["gyro_2_y"].append(float(values[12]))
            dict["gyro_2_z"].append(float(values[13]))
            dict["millis"].append(int(values[14]) - reset_time)
    return dict
'''
'''
def dict_to_graph(dict):
    temp_1_avg = statistics.mean(dict["temp_1"])
    temp_2_avg = statistics.mean(dict["temp_2"])
    print("temp 1 avg: " + str(temp_1_avg))
    print("temp 2 avg: " + str(temp_2_avg))
    # x axis values
    x = dict["millis"]
    # corresponding y axis values
    y = dict["temp_1"]
    # plotting the points 
    plt.plot(x, y)
    # naming the x axis
    plt.xlabel("Time")
    # naming the y axis
    plt.ylabel("Temp 1")
    # giving a title to my graph
    plt.title("Temp 1 graph")
    # function to show the plot
    plt.show()
'''

def main():
    #print("What file do you want to analyze?")
    #file_name = input()
    #f_array = read_file(file_name)
    f_array = read_file("MSA28.CSV")
    print(f_array)
    #for l in file_lines:
    #    print(l)
    #dict = lines_to_dict(file_lines)
    #for k in dict:
    #    print(k)
    #    print(dict[k])
    #dict_to_graph(dict)

if __name__ == "__main__":
    main()
