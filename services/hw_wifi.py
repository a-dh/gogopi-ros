import os
import sys
import time
import threading
import logging
import importlib
import pigpio
import pywifi
from hw_find import get_cpu_serial_number

log_file_path = "/boot/hiwonder_wifi.log"
config_file_name = "hiwonder_wifi_conf.py"
internal_config_file_dir_path = "/etc/Hiwonder"
external_config_file_dir_path = "/boot"
internal_config_file_path = os.path.join(internal_config_file_dir_path, config_file_name)
external_config_file_path = os.path.join(external_config_file_dir_path, config_file_name)

# LED blink timing in ms; modified by WIFI_MGR to reflect connection state
led_on_time = 100
led_off_time = 100

LED1_PIN = 16   # BCM GPIO 16 / BOARD pin 36 (active-low)
_pi = None


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
        ("create_ap --version > /dev/null 2>&1", "create_ap"),
        ("haveged > /dev/null 2>&1", "haveged"),
    ]:
        if os.system(cmd) not in (0, 256):
            logger.error(f"{name} not installed")
            exit_ = True
    if exit_:
        sys.exit(-1)


def led_setup():
    global _pi
    _pi = pigpio.pi()
    if not _pi.connected:
        raise RuntimeError("Cannot connect to pigpiod")
    _pi.set_mode(LED1_PIN, pigpio.OUTPUT)
    _pi.write(LED1_PIN, 1)  # active-low: start off


def led_thread():
    """Blink LED1 according to led_on_time / led_off_time (ms units, 10 ms tick)."""
    global led_on_time, led_off_time
    local_on = local_off = 0
    count = cycle_time = 0
    while True:
        if local_on != led_on_time or local_off != led_off_time:
            local_on = led_on_time
            local_off = led_off_time
            cycle_time = local_on + local_off
            count = 0
        if count < local_on:
            _pi.write(LED1_PIN, 0)  # active-low: on
            count += 1
        elif count < cycle_time:
            _pi.write(LED1_PIN, 1)  # active-low: off
            count += 1
        else:
            count = 0
        time.sleep(0.01)


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

    for path, cfg_dir in [
        (config_file_name, None),
        (internal_config_file_path, internal_config_file_dir_path),
        (external_config_file_path, external_config_file_dir_path),
    ]:
        if os.path.exists(path):
            if cfg_dir:
                sys.path.insert(0, cfg_dir)
            update_globals(os.path.splitext(config_file_name)[0])

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
        global HW_WIFI_AP_SSID, HW_WIFI_STA_SSID, HW_WIFI_AP_PASSWORD
        global HW_WIFI_STA_PASSWORD, led_on_time, led_off_time

        if HW_WIFI_MODE == 1:  # AP mode
            led_on_time = 50
            led_off_time = 50
            os.system("set +o history > /dev/null 2>&1")
            for proc in ("create_ap", "hostapd", "wpa_supplicant"):
                os.system(f"pkill {proc} > /dev/null 2>&1")
            if not isinstance(HW_WIFI_AP_PASSWORD, str):
                HW_WIFI_AP_PASSWORD = ""
            if 0 < len(HW_WIFI_AP_PASSWORD) < 8:
                HW_WIFI_AP_PASSWORD = ""
            if not isinstance(HW_WIFI_AP_SSID, str):
                HW_WIFI_AP_SSID = "HW-" + sn[:8]
            cmd = (
                f"create_ap -n wlan0 --no-virt -g {HW_WIFI_AP_GATEWAY}"
                f" --country {HW_WIFI_COUNTRY}"
                f" --freq-band {HW_WIFI_FREQ_BAND}"
                f" -c {HW_WIFI_CHANNEL}"
                f" {HW_WIFI_AP_SSID} {HW_WIFI_AP_PASSWORD}"
            )
            logger.info("Create AP: " + HW_WIFI_AP_SSID)
            t = threading.Thread(target=lambda: os.system(cmd), daemon=True)
            t.start()
            os.system("set -o history > /dev/null 2>&1")
            t.join()
            return -1

        elif HW_WIFI_MODE == 2:  # Client mode
            led_on_time = 5
            led_off_time = 5
            for proc in ("create_ap", "hostapd", "wpa_supplicant"):
                os.system(f"pkill {proc} > /dev/null 2>&1")
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
                    led_on_time = 100
                    led_off_time = 0
                    break
                time.sleep(1)
            else:
                logger.error("Cannot connect to SSID: " + HW_WIFI_STA_SSID)
                return 0

            t.join()
            return -1

        else:
            logger.error("Invalid HW_WIFI_MODE")

    if HW_WIFI_LED:
        threading.Thread(target=led_thread, daemon=True).start()

    while True:
        if WIFI_MGR() == -1:
            sys.exit(0)
        HW_WIFI_MODE = 1
        HW_WIFI_AP_SSID = "HW-" + sn[:8]
        HW_WIFI_AP_PASSWORD = ""
