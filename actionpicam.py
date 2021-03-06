#!/usr/bin/env python2.7
# script by Alex Eames http://RasPi.tv
# needs RPi.GPIO 0.5.2 or later
# updated to work on the A+

# Notes for wiki
# sudo apt-get install -y gpac
# crontab -e
# --> @reboot sh /home/pi/actionpicam/launcher.sh >/home/pi/logs/cronlog 2>&1

import RPi.GPIO as GPIO
import subprocess
import signal
import threading
from time import sleep
import time
import sys
import os

GPIO.setmode(GPIO.BOARD)

led_started = 36   #LED to know when the app is running
led_recording = 38 #LED to know if it is recording
led_picture = 37   #LED (bright LED) to illuminate subject while taking a picture
leds = [led_recording, led_started, led_picture]

button_record = 33
button_stop = 35
button_picture = 40

# GPIO 33 & 40 set up as input, pulled up to avoid false detection.
# Both ports are wired to connect to GND on button press.
# So we'll be setting up falling edge detection for both
GPIO.setup(button_record, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(button_picture, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# GPIO 35 set up as an input, pulled down, connected to 3V3 on button press
GPIO.setup(button_stop, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

# Set up LEDs (36, 37 and 38)
for led in leds:
    GPIO.setup(led, GPIO.OUT)

app_path = os.path.dirname(os.path.realpath(__file__)) + os.sep

video_popen = None
video_path = app_path + "video" + os.sep
vid_rec_num = "vid_rec_num.txt"
vid_rec_num_fp = app_path + vid_rec_num # need full path if run from rc.local
base_vidfile = "raspivid -t 3600000 -o " + video_path

pic_path = app_path + "photo" + os.sep
pic_rec_num = "pic_rec_num.txt"
pic_rec_num_fp = app_path + pic_rec_num # need full path if run from rc.local
base_picfile = "raspistill -r -t 1 -o " + pic_path

time_off = time.time()

class ThreadConvert(threading.Thread):
    def __init__(self,filename):
        super(ThreadConvert, self).__init__()
        self._filename = filename

    def run(self):
        # fl = do_the_flashing([led_recording],0.2)
        subprocess.call (["MP4Box -add %s.h264 %s.mp4" % (self._filename, self._filename)], shell=True)
        os.remove("%s.h264" % (self._filename))
        # fl.stop()

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.isSet()

class FlashLeds(threading.Thread):
    def __init__(self,leds,interval):
        super(FlashLeds, self).__init__()
        self._stop = threading.Event()
        self._leds = leds
        self._interval = interval

    def run(self):
        while True:
            for led in self._leds:
                GPIO.output(led, 1)
                sleep(self._interval)
                GPIO.output(led, 0)
                sleep(self._interval)

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.isSet()

    def __del__(self):
        for led in self._leds:
            GPIO.output(led, 0)

def do_the_flashing(leds_to_flash,interval,wait=None):
    fl = FlashLeds(leds_to_flash,interval)
    fl.daemon = True
    fl.start()
    if(wait is not None):
        sleep(wait)
        fl.stop()
        return None
    else:
        return fl

def root_path():
    return os.path.abspath(os.sep)

def write_rec_num(which):
    global video_rec_num
    global picture_rec_num
    if(which == "video"):
        vrnw = open(vid_rec_num_fp, 'w')
        vrnw.write(str(video_rec_num))
        vrnw.close()
    elif (which == "picture"):
        prnw = open(pic_rec_num_fp, 'w')
        prnw.write(str(picture_rec_num))
        prnw.close()

def get_filename(rec_num, extension):
    filename = str(rec_num).zfill(5)
    if extension != "":
        filename = "%s.%s" % (filename, extension)
    return filename

def start_recording(rec_num):
    global video_popen
    if video_popen is None:
        vidfile = base_vidfile + get_filename(rec_num, "h264")
        vidfile += " -fps 25 -b 15000000 -vs" #-w 1280 -h 720 -awb tungsten
        print "starting recording\n%s" % vidfile
        time_now = time.time()
        if (time_now - time_off) >= 0.3:
            GPIO.output(led_recording, 1)
            video_popen = subprocess.Popen ( vidfile.split() )

def take_picture(rec_num):
    picfile = base_picfile + get_filename(rec_num, "raw")
    print "Taking picture\n%s" % picfile
    GPIO.output(led_picture, 1)
    subprocess.call ([picfile], shell=True)
    GPIO.output(led_picture, 0)

#### Quality VS length ###
# on long clips at max quality you may get dropouts
# -w 1280 -h 720 -fps 25 -b 3000000 
# seems to be low enough to avoid this 

def stop_recording():
    global video_rec_num
    global time_off
    global video_popen
    if video_popen is not None and video_popen.poll() is None:
        time_off = time.time()
        print "Stopping recording"
        GPIO.output(led_recording, 0)
        video_popen.send_signal ( signal.SIGINT )
        video_popen = None

        #Convert to MP4
        filename = video_path + get_filename(video_rec_num, "")
        if(os.path.isfile("%s.h264" % (filename) )):
            print "Converting video to MP4"
            tc = ThreadConvert(filename)
            tc.start()
        else :
            print "File %s.h264 does not exists" % (filename)

        space_used()     # display space left on recording drive

def space_used():    # function to display space left on recording device
    output_df = subprocess.Popen(["df", "-Ph", root_path()], stdout=subprocess.PIPE).communicate()[0]
    it_num = 0
    for line in output_df.split("\n"):
        line_list = line.split()
        if it_num == 1:
            storage = line_list
        it_num += 1
    print "Card size: %s,   Used: %s,    Available: %s,    Percent used: %s" % (storage[1], storage[2], storage[3], storage[4])
    percent_used = int(storage[4][0:-1])
    if percent_used > 95:
        print "Watch out, you've got less than 5% space left on your SD card!"

# threaded callback function runs in another thread when event is detected
# this increments variable rec_num for filename and starts recording
def record_video_callback(channel):
    global video_rec_num
    global video_popen
    time_now = time.time()
    if (time_now - time_off) >= 0.3:
        print "record button pressed"
        if video_popen is None:
            video_rec_num += 1
            write_rec_num("video")
            start_recording(video_rec_num)

def take_picture_callback(channel):
    global picture_rec_num
    time_now = time.time()
    if (time_now - time_off) >= 0.3:
        print "picture button pressed"
        picture_rec_num += 1
        write_rec_num("picture")
        take_picture(picture_rec_num)

def cleanup():
    for led in leds:
        GPIO.output(led, 0)
    GPIO.cleanup()

def shutdown():
    print "shutting down now"
    stop_recording()
    fl = do_the_flashing(leds,0.05,3)
    cleanup()
    os.system("sudo halt")
    sys.exit()

if not os.path.exists(app_path):
    os.mkdir(app_path)

if not os.path.exists(video_path):
    os.mkdir(video_path)

if not os.path.exists(pic_path):
    os.mkdir(pic_path)

print "Make sure you have a record video button connected so that when pressed"
print "it will connect GPIO port 33 to GND\n"
print "Make sure you have a take picture button connected so that when pressed"
print "it will connect GPIO port 40 to GND\n"
print "Make sure you have a stop recording / shutdown button connected so that when pressed"
print "it will connect GPIO port 35 to 3V3\n"

# when a falling edge is detected on port 33 my_callback2() will be run
GPIO.add_event_detect(button_record, GPIO.FALLING, callback=record_video_callback)

# when a falling edge is detected on port 25, my_callback() will be run
GPIO.add_event_detect(button_picture, GPIO.FALLING, callback=take_picture_callback)

#App is up and running
GPIO.output(led_started, 1)

# check rec_num from file
try:
    directory_data = os.listdir(app_path)
    if vid_rec_num in directory_data:

        # read file vid_rec_num, make into int() set rec_num equal to it
        vrn = open(vid_rec_num_fp, 'r')
        video_rec_num = int(vrn.readline())
        print "video_rec_num is %d" % video_rec_num
        vrn.close() 

    # if file doesn't exist, create it to avoid issues later
    else:
        video_rec_num = 0
        write_rec_num("video")

    if pic_rec_num in directory_data:

        # read file vid_rec_num, make into int() set rec_num equal to it
        prn = open(pic_rec_num_fp, 'r')
        picture_rec_num = int(prn.readline())
        print "picture_rec_num is %d" % picture_rec_num
        prn.close()

    # if file doesn't exist, create it to avoid issues later
    else:
        picture_rec_num = 0
        write_rec_num("picture")

except:
    print("Problem listing " + app_path)
    fl = do_the_flashing(leds,0.1,2)
    cleanup()
    sys.exit()

try:
    while True:
        # this will run until button attached to 35 is pressed, then 
        # if pressed long, shut program, if pressed very long shutdown Pi
        # stop recording and shutdown gracefully
        print "Waiting for button press" # rising edge on port 35
        GPIO.wait_for_edge(button_stop, GPIO.RISING)

        print "Stop button pressed"
        stop_recording()

        # poll GPIO 35 button at 20 Hz continuously for 3 seconds
        # if at the end of that time button is still pressed, shut down
        # if it's released at all, break
        for i in range(60):
            if not GPIO.input(button_stop):
                break
            sleep(0.05)

        if 25 <= i < 58:              # if released between 1.25 & 3s close prog
            print "Closing program"
            fl = do_the_flashing(leds,0.02,2)
            cleanup()
            sys.exit()

        if GPIO.input(button_stop):
            if i >= 59:
                shutdown()

except KeyboardInterrupt:
    stop_recording()
    cleanup()
