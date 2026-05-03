# Command a single PWM servo.
# Set use_pulse=true to position by pulse width; false to position by angle.
uint8  servo_id      # 1-6
bool   use_pulse     # true → pulse_us, false → angle_deg
uint16 pulse_us      # 500-2500 µs (used when use_pulse=true)
uint8  angle_deg     # 0-180 ° (used when use_pulse=false)
uint16 move_time_ms  # transition time in ms (only applies to pulse mode)
