# GoGoPi-Ros
Ros2 on a GoGoPi mobile robot with a YDLIDAR scanning ranger added

## The base robot

Original source: [HiWonder.hk](https://www.hiwonder.hk/collections/raspberry-pi/products/hiwonder-gogopi-raspberry-pi-4b-intelligent-vision-robot-car-python-program)

A Canadian source: [Robotshop.ca](https://www.robotshop.com/ca/en/hiwonder-gogopi-raspberry-pi-4b-intelligent-vision-robot-car-python-program.html)

## Arbitrary Choices

### Ros-galactic for stability + future proofing

Yes galactic is in development, but it has not caused problems yet :)

    deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu focal main


### Rationalized choices

#### Use the Hiwonder Power Off button code.

It works Maybe something better will come up

#### Default to pre-configured Wifi Authentication

To make logging in from development labs a bit easier

#### Add a Wifi Failover to AP mode at some point

Needed for demonstrations if nothing else.

#### Python3 only ASAP. 

Ros2 precedent(?)

Python3 is what all useful libraries will support. 

Some porting of the examples will be necessary

## Current investigations

### Controllng a YDLIDAR TX8 spin motor with the RPI 4 serial port

[uhubctl v 2.4.1](https://github.com/mvp/uhubctl) controls the RPI hub power, but that is a coarse control that prevents using other USB stuff.
