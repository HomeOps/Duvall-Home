import serial
import sys
import getopt
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

DEST=sys.argv[1]
#MD="/dev/ttyUSB2"
MD="COM10"
if (len(sys.argv) > 2):
    message=sys.argv[2]
else:
    message = ''
    for line in sys.stdin:
        message+=line

logging.debug('Sending message '+message+' to '+DEST)

logging.debug('Opening serial port '+MD)
ser = serial.Serial(MD)  # open serial port
ser.write(b'\x1a') # Flush any previuos attempts to send message
ser.flush()

logging.debug('Sending AT+CMGF command')

line = b''
ser.write(b'AT+CMGF=1\r')
while line!=b'OK\r\n':
  line = ser.readline()
  logging.debug('Response '+str(line))

ser.flush()

logging.debug('Sending AT+CMGS command')
line = b''
phoneAsAscii = bytes(DEST, 'ascii')
ser.write(b'AT+CMGS="'+phoneAsAscii+b'"\r')
while line!=b'> \r\n':
  line = ser.readline()
  logging.debug('Response '+str(line))

ser.flush()
logging.debug('Sending message')
messageAsAscii = bytes(message, 'ascii')
ser.write(messageAsAscii + b'\x1a')
line = b''
while line!=b'OK\r\n':
  line = ser.readline()
  logging.debug('Response '+str(line))

ser.close() 
