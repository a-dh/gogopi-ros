from setuptools import setup
import os
from glob import glob

package_name = "gogopi_bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "urdf"), glob("urdf/*.urdf")),
    ],
    install_requires=[
        "setuptools",
        "opencv-python-headless",
        "adafruit-blinka",
        "adafruit-circuitpython-mlx90640",
    ],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            "board_node = gogopi_bringup.board_node:main",
            "sonar_node = gogopi_bringup.sonar_node:main",
            "camera_node = gogopi_bringup.camera_node:main",
            "thermal_node = gogopi_bringup.thermal_node:main",
        ],
    },
)
