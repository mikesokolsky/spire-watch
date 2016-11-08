import datetime
import math
import sys
import threading
import time

import predict
import requests
import serial


class Satellite(object):
    """Represents a Satellite to track"""

    LOGIN_URL = 'https://www.space-track.org/ajaxauth/login'
    QUERY_STRING = 'https://www.space-track.org/basicspacedata/query/class/'\
        'tle_latest/ORDINAL/1/NORAD_CAT_ID/{}/orderby/TLE_LINE1 ASC/format/3le'
    # How long to cache the TLE for (in seconds)
    TLE_REFRESH_TIME = 60 * 60 * 6

    def __init__(self, norad_id):
        self.id = norad_id
        self.tle = None
        self.update_tle()
        self.this_wont_work()

    def _request_tle(self):
        return requests.post(self.LOGIN_URL,
            data={'identity': '',
                  'password': '',
                  'query': self.QUERY_STRING.format(self.id)
                  })

    def update_tle(self):
        response = self._request_stle()
        self.tle = response.text.replace('\r', '')

    @property
    def name(self):
        if self.tle:
            return self.tle.split('\n')[0][2:].encode('latin-1')
        return None


class Groundstation(object):
    """Represents the groundstation we are tracking from"""

    GEO_URL = 'http://freegeoip.net/json/'
    SAT_LIST_URL = 'https://s3-us-west-2.amazonaws.com/ardustation/satellites'
    SAT_LIST_REFRESH_TIME = 60 * 60 * 6

    def __init__(self, latitude=None, longitude=None, altitude=0):
        """If latitude and longitude are not specified, it will try to
        estimate location using the IP address.  Altitude (in meters) will
        always default to 0 unless specified.
        Latitude is positive North
        Longitude is positive East
        Introduced a bug
        """
        if latitude and longitude:
            self.longitude = longitude
        else:
            loc = requests.get(self.GEO_URL).json()
            self.latitude = loc['latitude']
            self.longitude = loc['longitude']
            print loc
        self.altitude = altitude
        # For first run update the satellite list and wait for it to complete
        self.update_satellite_list(block=True)

    def update_satellite_list(self, block=False):
        """Gets list of satellites from the server, retrying if necessary"""
        if hasattr(self, '_update_thread') and self._update_thread.is_alive():
            return
        self._update_thread = threading.Thread(
            target=self._update_satellite_list)
        self._update_thread.start()
        if block:
            self._update_thread.join()
        self.sat_list_last_checked = datetime.datetime.utcnow()

    def _update_satellite_list(self):
        retry_time = 10
        norad_ids = None
        while norad_ids is None:
            try:
                response = requests.get(self.SAT_LIST_URL)
                if response.status_code != 200:
                    raise requests.exceptions.HTTPError()
                norad_ids = response.text.split()
            except (requests.exceptions.Timeout,
                    requests.exceptions.HTTPError,
                    requests.exceptions.ConnectionError):
                print "Error getting satellite List"
                time.sleep(retry_time)
                retry_time *= 2
                if retry_time > self.SAT_LIST_REFRESH_TIME:
                    return_time = self.SAT_LIST_REFRESH_TIME
        self._satellite_list = map(lambda n: Satellite(n), norad_ids)

    @property
    def satellite_list(self):
        if not self.sat_list_last_checked or self.sat_list_last_checked +\
                datetime.timedelta(seconds=self.SAT_LIST_REFRESH_TIME) <\
                datetime.datetime.utcnow():
            self.update_satellite_list()
        return self._satellite_list

    @property
    def predict_qth(self):
        """Tuple containing lat, lon, alt for predict (using NW positive)"""
        return (self.latitude, -self.longitude, self.altitude)

    def observe(self, sat):
        """Get prediction of the satellite location, and relative azimuth and
        elevation given this groundstation"""
        return predict.observe(sat.tle, self.predict_qth)

    def transits(self, sat):
        """Get upcoming transits of a satellite"""
        return predict.transits(sat.tle, self.predict_qth)

    def next_transit(self):
        sats = self.satellite_list
        transits_list = map(self.transits, sats)
        transits_list = filter(lambda x: x is not None, transits_list)
        if len(transits_list) == 0:
            return None
        next_transit = None
        sat = None
        for index, transits in enumerate(transits_list):
            try:
                transit = transits.next()
            except predict.PredictException:
                continue
            if next_transit is None or transit.start < next_transit.start:
                next_transit = transit
                sat = sats[index]
        return (sat, next_transit)

    def all_observations(self):
        return map(self._observe, self.satellite_list)


class ArduinoError(Exception):
    pass


def uses_com(func):
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except serial.SerialException, IOError:
            self._com.close()
            self._com = None
            print "Arduino disconnected"
    return wrapper


class Arduino(object):

    HEADER = '\n'
    SAT_NAME = '0'
    TIME = '1'
    TRACKING = '2'
    MAG = '3'
    MAX_STRING_LEN = 21
    END = '\0'
    BAUDRATE = 57600
    TIMEOUT = 1

    def __init__(self, port):
        self._com = None
        self.port = port
        self._connect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._com:
            self._com.close()

    def _connect(self):
        """Tries to connect to Arduino, retrying every 5 seconds on failure"""
        while self._com is None:
            try:
                self._com = serial.Serial()
                self._com.baudrate = self.BAUDRATE
                self._com.port = self.port
                self._com.setDTR(False)
                self._com.timeout = self.TIMEOUT
                self._com.open()
                # Wait for arduino to reset
                time.sleep(2)
            except serial.SerialException as e:
                print "Unable to connect to Arduinio, retrying..."
                self._com = None
                time.sleep(5)

    @property
    def com(self):
        if self._com is None:
            self._connect()
        return self._com

    @uses_com
    def set_sat_name(self, text):
        extra = self.MAX_STRING_LEN - len(text)
        spacer = ' ' * (extra / 2)
        text = spacer + text + spacer
        cmd = self.HEADER + self.SAT_NAME + text + self.END
        self.com.write(cmd)
        response = self.com.read(2)
        if len(response) != 2 or response[0] != self.HEADER:
            print "No response from Arduino :("

    @uses_com
    def set_time(self, text):
        self.com.write(self.HEADER + self.TIME + text + self.END)
        response = self.com.read(2)
        if len(response) != 2 or response[0] != self.HEADER:
            print "No response from Arduino :("

    @classmethod
    def angle_to_ms(cls, angle):
        min_ms = 544
        max_ms = 2400
        ms = angle / 180. * (max_ms - min_ms) + min_ms
        return int(ms)

    @uses_com
    def set_tracking(self, azimuth=None, elevation=None):
        if azimuth is not None and elevation is not None:
            azimuth = Arduino.angle_to_ms(azimuth)
            elevation = Arduino.angle_to_ms(elevation)
            self.com.write(self.HEADER + self.TRACKING + 'a' + str(azimuth) + self.END)
            self.com.write(self.HEADER + self.TRACKING + 'e' + str(elevation) + self.END)
        else:
            self.com.write(self.HEADER + self.TRACKING + '0' + self.END)
        response = self.com.read(2)
        if len(response) != 2 or response[0] != self.HEADER:
            print "No response from Arduino :("

    @uses_com
    def magnetometer(self):
        """Send write_data to the Arduino after attaching a header byte
        Then wait up to TIMEOUT for 5 bytes back, the header followed by
        4 data bytes

        Returns - 4 data bytes
        Raises - ArduinoError if something goes wrong
        """
        self.com.reset_input_buffer()
        self.com.write(self.HEADER + self.MAG + self.END)
        header = self.com.read(1)
        if header != self.HEADER:
            print "Got bad header from Arduino"
            raise ArduinoError()
        data = ''
        while len(data) < 15:
            read_data = self.com.read(1)
            if len(read_data) != 1:
                print "Error reading from Arduino"
                raise ArduinoError()
            data += read_data
            if read_data == self.END:
                break
        print "Arduino mag data:", data
        mag_x = int(data[:data.index(',')])
        mag_y = int(data[data.index(',') + 1:-1])
        return mag_x, mag_y


class Compass(object):

    def __init__(self, arduino):
        self.arduino = arduino

    def get_heading(self):
        """Returns heading from 0-359 degrees"""
        raise NotImplementedError()


class ServoInterface(object):

    def __init__(self, arduino):
        self._arduino = arduino

    def set_position(self, az_pos, el_pos):
        """Set position of servos"""
        raise NotImplementedError()


class Heading(object):

    def __init__(self, alpha=0.2):
        self.heading = 0
        self.alpha = alpha

    def update(self, obs):
        self.heading = obs * self.alpha + (1 - self.alpha) * self.heading

if __name__ == '__main__':
    arduino = Arduino(sys.argv[1])
    heading = Heading(alpha=0.5)
    if len(sys.argv) > 2:
        arduino.set_tracking(False)
        arduino.set_sat_name("OSCAR 7")
        while True:
            #arduino.set_tracking(False)
            arduino.set_sat_name("OSCAR 7")
            #arduino.set_time('In %d:%02d:%02d' % (0, 59, 23))
            time.sleep(0.1)
            x, y = arduino.magnetometer()
            compass = math.atan2(y, x) * 180 / math.pi
            print compass
            offset = 110
            compass += offset
            heading.update(compass)
            obs = {'azimuth': 1, 'elevation': 45}
            az = obs['azimuth'] - heading.heading
            if az < 0:
                az += 360
            if az >= 360:
                az -= 360
            az = 360 - az
            el = obs['elevation']
            if az > 180:
                az -= 180
                el = 180 - el
            el += 10
            if el < 20:
                el = 20
            if el > 180:
                el = 180
            arduino.set_tracking(azimuth=az, elevation=el)
            time.sleep(0.2)

    gs = Groundstation()

    while True:
        sat, next_transit = gs.next_transit()
        if sat is not None and next_transit is not None:
            transit_start = datetime.datetime.fromtimestamp(next_transit.start)
            transit_end = datetime.datetime.fromtimestamp(next_transit.end)
            arduino.set_tracking(False)
            arduino.set_sat_name(sat.name)
            while transit_start > datetime.datetime.now():
                time_til = (transit_start - datetime.datetime.now()).seconds
                secs = time_til % 60
                time_til = time_til / 60
                mins = time_til % 60
                hours = time_til / 60
                arduino.set_sat_name(sat.name)
                arduino.set_time('In %d:%02d:%02d' % (hours, mins, secs))
                time.sleep(0.2)

            while transit_end > datetime.datetime.now():
                obs = gs.observe(sat)
                az = obs['azimuth']
                el = obs['elevation']
                print az
                offset = -110
                az += offset
                if az < 0:
                    az += 360
                if az >= 360:
                    az -= 360
                az = 360 - az
                if az > 180:
                    az -= 180
                    el = 180 - el
                el += 10
                if el < 20:
                    el = 20
                if el > 180:
                    el = 180
                print az, el
                arduino.set_tracking(azimuth=az, elevation=el)
                arduino.set_sat_name(sat.name)
                arduino.set_time('')
                time.sleep(0.2)

        else:
            self.arduino.set_sat_name('Launch more sats')
            self.arduino.set_time('No upcoming passes')
            while True:
                pass
