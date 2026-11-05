# Working Method

1. The sensor takes a soil-moisture reading at a defined interval.
2. The controller validates the reading and compares it with the configured lower moisture limit.
3. If the soil is too dry and the system is in an allowed watering period, the controller activates the pump or valve.
4. Watering stops when the target moisture level is reached or a maximum run-time limit is reached.
5. The system stores the event and sends an alert if a sensor fault or abnormal condition is detected.