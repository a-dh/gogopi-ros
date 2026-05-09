import os
import subprocess
import sys
import time
import threading
import logging
import importlib
import pigpio
import pywifi


def get_cpu_serial_number():
    for line in open("/proc/cpuinfo"):
        if line.startswith("Serial"):
            return line.split(":")[-1].strip()[-16:].upper()
    return "0000000000000000"

log_file_path = "/boot/hiwonder_wifi.log"
config_file_name = "hiwonder_wifi_conf.py"
internal_config_file_dir_path = "/etc/Hiwonder"
external_config_file_dir_path = "/boot"
internal_config_file_path = os.path.join(internal_config_file_dir_path, config_file_name)
external_config_file_path = os.path.join(external_config_file_dir_path, config_file_name)

LED1_PIN = 16   # BCM GPIO 16 / BOARD pin 36 (active-low)
_pi = None


def led_setup():
    global _pi
    _pi = pigpio.pi()
    if not _pi.connected:
        raise RuntimeError("Cannot connect to pigpiod")
    _pi.set_mode(LED1_PIN, pigpio.OUTPUT)
    _set_led(0, 255)  # start off


def _set_led(on_ms, off_ms):
    """Set LED1 blink pattern via pigpio software PWM (active-low).

    on_ms=100, off_ms=0   → solid on
    on_ms=0,   off_ms=100 → solid off
    on_ms=50,  off_ms=50  → 10 Hz blink
    on_ms=5,   off_ms=5   → 100 Hz rapid blink
    """
    if off_ms == 0:
        _pi.set_PWM_dutycycle(LED1_PIN, 0)    # always LOW → LED on
    elif on_ms == 0:
        _pi.set_PWM_dutycycle(LED1_PIN, 255)  # always HIGH → LED off
    else:
        freq = max(1, round(1000 / (on_ms + off_ms)))
        duty = round(255 * off_ms / (on_ms + off_ms))  # HIGH fraction = off fraction (active-low)
        _pi.set_PWM_frequency(LED1_PIN, freq)
        _pi.set_PWM_dutycycle(LED1_PIN, duty)


def update_globals(module):
    if module in sys.modules:
        mdl = importlib.reload(sys.modules[module])
    else:
        mdl = importlib.import_module(module)
    names = mdl.__dict__.get("__all__") or [x for x in mdl.__dict__ if not x.startswith("_")]
    globals().update({k: getattr(mdl, k) for k in names})


def check_dependens(logger):
    exit_ = False
    for cmd, name in [
        ("hostapd -v > /dev/null 2>&1", "hostapd"),
        ("ip a > /dev/null 2>&1", "iproute2"),
        ("dnsmasq --version > /dev/null 2>&1", "dnsmasq"),
        ("iw > /dev/null 2>&1", "iw"),
        ("haveged > /dev/null 2>&1", "haveged"),
    ]:
        if os.system(cmd) not in (0, 256):
            logger.error(f"{name} not installed")
            exit_ = True
    if exit_:
        sys.exit(-1)


def start_ap(ssid, password, gateway, country, freq_band, channel, logger):
    iface = "wlan0"
    hw_mode = "a" if float(freq_band) >= 5.0 else "g"

    for proc in ("hostapd", "dnsmasq", "wpa_supplicant"):
        subprocess.call(["pkill", proc], stderr=subprocess.DEVNULL)
    time.sleep(0.5)

    subprocess.call(["ip", "link", "set", iface, "up"])
    subprocess.call(["ip", "addr", "flush", "dev", iface])
    subprocess.call(["ip", "addr", "add", f"{gateway}/24", "dev", iface])

    hostapd_lines = [
        f"interface={iface}",
        f"ssid={ssid}",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        f"country_code={country}",
        "ieee80211n=1",
        "wmm_enabled=1",
        "auth_algs=1",
    ]
    if password:
        hostapd_lines += [
            "wpa=2",
            f"wpa_passphrase={password}",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
        ]

    net_prefix = ".".join(gateway.split(".")[:3])
    dnsmasq_lines = [
        f"interface={iface}",
        "bind-interfaces",
        "port=0",  # disable DNS, DHCP only — avoids conflict with systemd-resolved
        f"dhcp-range={net_prefix}.100,{net_prefix}.200,255.255.255.0,24h",
        f"dhcp-option=3,{gateway}",
    ]

    hostapd_conf = "/tmp/gogopi_hostapd.conf"
    dnsmasq_conf = "/tmp/gogopi_dnsmasq.conf"
    with open(hostapd_conf, "w") as f:
        f.write("\n".join(hostapd_lines) + "\n")
    with open(dnsmasq_conf, "w") as f:
        f.write("\n".join(dnsmasq_lines) + "\n")

    logger.info("Starting dnsmasq for DHCP on %s (%s.100–200)", iface, net_prefix)
    dnsmasq_proc = subprocess.Popen(
        ["dnsmasq", "--no-daemon", f"--conf-file={dnsmasq_conf}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    logger.info("Starting hostapd: SSID=%s hw_mode=%s channel=%s", ssid, hw_mode, channel)
    hostapd_proc = subprocess.Popen(
        ["hostapd", hostapd_conf],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    hostapd_proc.wait()
    logger.info("hostapd exited (rc=%d)", hostapd_proc.returncode)

    dnsmasq_proc.terminate()
    dnsmasq_proc.wait()




if __name__ == "__main__":
    sn = get_cpu_serial_number()
    led_setup()

    HW_WIFI_MODE = 1
    HW_WIFI_AP_SSID = "HW-" + sn[:8]
    HW_WIFI_STA_SSID = ""
    HW_WIFI_AP_PASSWORD = ""
    HW_WIFI_STA_PASSWORD = ""
    HW_WIFI_AKM_TYPE = pywifi.const.AKM_TYPE_WPA2PSK
    HW_WIFI_CIPHER_TYPE = pywifi.const.CIPHER_TYPE_CCMP
    HW_WIFI_CHANNEL = 7
    HW_WIFI_AP_GATEWAY = "192.168.149.1"
    HW_WIFI_COUNTRY = "US"
    HW_WIFI_FREQ_BAND = 2.4
    HW_WIFI_TIMEOUT = 30
    HW_WIFI_RESET_NOW = False
    HW_WIFI_LED = True

    logger = logging.getLogger("Hiwonder WiFi tool")
    logger.setLevel(logging.DEBUG)
    log_handler = logging.FileHandler(log_file_path)
    log_handler.setLevel(logging.INFO)
    log_handler.setFormatter(
        logging.Formatter("%(name)s - %(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(log_handler)

    check_dependens(logger)

    config_found = False
    for path, cfg_dir in [
        (config_file_name, None),
        (internal_config_file_path, internal_config_file_dir_path),
        (external_config_file_path, external_config_file_dir_path),
    ]:
        if os.path.exists(path):
            if cfg_dir:
                sys.path.insert(0, cfg_dir)
            update_globals(os.path.splitext(config_file_name)[0])
            config_found = True

    if not config_found:
        logger.info("No WiFi config found — leaving existing WiFi setup unchanged")
        _set_led(100, 0)  # solid on: assume existing connection is up
        sys.exit(0)

    if HW_WIFI_RESET_NOW:
        for p in [config_file_name, internal_config_file_path, external_config_file_path]:
            os.system(f"rm {p} > /dev/null 2>&1")
        for d in [internal_config_file_dir_path, external_config_file_dir_path]:
            os.system(f"rm {d}/__pycache__ -rf > /dev/null 2>&1")
        HW_WIFI_MODE = 1
        HW_WIFI_AP_SSID = "HW-" + sn[:8]
        HW_WIFI_AP_PASSWORD = ""
        HW_WIFI_CHANNEL = 7
        HW_WIFI_AP_GATEWAY = "192.168.149.1"
        HW_WIFI_COUNTRY = "US"
        HW_WIFI_FREQ_BAND = 2.4

    def WIFI_MGR():
        global HW_WIFI_AP_SSID, HW_WIFI_STA_SSID, HW_WIFI_AP_PASSWORD, HW_WIFI_STA_PASSWORD

        if HW_WIFI_MODE == 1:  # AP mode
            if HW_WIFI_LED:
                _set_led(50, 50)   # 10 Hz blink
            if not isinstance(HW_WIFI_AP_PASSWORD, str):
                HW_WIFI_AP_PASSWORD = ""
            if 0 < len(HW_WIFI_AP_PASSWORD) < 8:
                HW_WIFI_AP_PASSWORD = ""
            if not isinstance(HW_WIFI_AP_SSID, str):
                HW_WIFI_AP_SSID = "HW-" + sn[:8]
            start_ap(HW_WIFI_AP_SSID, HW_WIFI_AP_PASSWORD, HW_WIFI_AP_GATEWAY,
                     HW_WIFI_COUNTRY, HW_WIFI_FREQ_BAND, HW_WIFI_CHANNEL, logger)
            return -1

        elif HW_WIFI_MODE == 2:  # Client mode
            if HW_WIFI_LED:
                _set_led(5, 5)     # 100 Hz rapid blink
            for proc in ("hostapd", "dnsmasq", "wpa_supplicant"):
                subprocess.call(["pkill", proc], stderr=subprocess.DEVNULL)
            logger.info("Connecting to " + HW_WIFI_STA_SSID)
            t = threading.Thread(
                target=lambda: os.system("wpa_supplicant -iwlan0 -C /var/run/wpa_supplicant"),
                daemon=True,
            )
            t.start()
            time.sleep(2)

            profile = pywifi.Profile()
            profile.auth = pywifi.const.AUTH_ALG_OPEN
            profile.akm.append(HW_WIFI_AKM_TYPE)
            profile.cipher = HW_WIFI_CIPHER_TYPE
            profile.ssid = HW_WIFI_STA_SSID
            profile.key = HW_WIFI_STA_PASSWORD

            wifi = pywifi.PyWiFi()
            iface = wifi.interfaces()[1]
            iface.remove_all_network_profiles()
            iface.connect(iface.add_network_profile(profile))

            for _ in range(HW_WIFI_TIMEOUT):
                if iface.status() == pywifi.const.IFACE_CONNECTED:
                    logger.info("Connected to " + HW_WIFI_STA_SSID)
                    threading.Thread(
                        target=lambda: os.system("dhclient wlan0"), daemon=True
                    ).start()
                    if HW_WIFI_LED:
                        _set_led(100, 0)   # solid on
                    break
                time.sleep(1)
            else:
                logger.error("Cannot connect to SSID: " + HW_WIFI_STA_SSID)
                return 0

            t.join()
            return -1

        else:
            logger.error("Invalid HW_WIFI_MODE")

    while True:
        if WIFI_MGR() == -1:
            sys.exit(0)
        HW_WIFI_MODE = 1
        HW_WIFI_AP_SSID = "HW-" + sn[:8]
        HW_WIFI_AP_PASSWORD = ""
