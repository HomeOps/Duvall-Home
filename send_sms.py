import asyncio
import serial_asyncio
import time
import serial
import sys
import getopt

DEST=sys.argv[1]
#MD="/dev/ttyUSB2"
MD="COM10"
message="Hello World"

class GSMModem(asyncio.Protocol):
    def connection_made(self, transport):
        self.transport = transport
        print('port opened', transport)
        transport.serial.rts = False  # You can manipulate Serial object via transport
        print('AT+CMGF')
        transport.write(b'AT+CMGF=1\n')
        time.sleep(5)
        print('AT+CMGS')
        transport.write(b'AT+CMGS="+1XXXXXXXX"\n')
        time.sleep(5)
        print('message')
        transport.write(b'Hello World\x1a\n')
        time.sleep(5)

    def data_received(self, data):
        print('data received', repr(data))
        if b'\n' in data:
            self.transport.close()

    def connection_lost(self, exc):
        print('port closed')
        self.transport.loop.stop()

    def pause_writing(self):
        print('pause writing')
        print(self.transport.get_write_buffer_size())

    def resume_writing(self):
        print(self.transport.get_write_buffer_size())
        print('resume writing')

async def send_sms():
    print('send_sms')
    loop = asyncio.get_event_loop()
    connection = serial_asyncio.create_serial_connection(loop, GSMModem, MD, baudrate=115200)
    tuple = await connection
    transport = tuple[0]
    print('AT+CMGF')
    transport.write(b'AT+CMGF=1\n')
    time.sleep(5)
    #DEST = b'+1XXXXXX' #.encode('ascii','replace')
    print('AT+CMGS')
    transport.write(b'AT+CMGS="+1XXXXXXX"\n')
    time.sleep(5)
    #message = b'Hello World\n'#.encode('ascii','replace')
    print('message')
    transport.write(b'Hello World\x1a\n')
    time.sleep(5)
    #transport.data_received()
    #msg = await transport.read();
    #print(msg)

print('Sending message '+message+' to '+DEST)

print('AT+CMGF')
ser = serial.Serial(MD)  # open serial port
ser.write(b'\x1a') # Flush any previuos attempts to send message
ser.flush()
print('AT+CMGF')
line = b''
ser.write(b'AT+CMGF=1\r')
while line!=b'OK\r\n':
  line = ser.readline()
  print(line)

ser.flush()
print('AT+CMGS')
line = b''
phoneAsAscii = bytes(DEST, 'ascii')
ser.write(b'AT+CMGS="'+phoneAsAscii+b'"\r')
while line!=b'> \r\n':
  line = ser.readline()
  print(line)

ser.flush()
print('message')
ser.write(b'Hello World\x1a')
line = b''
while line!=b'OK\r\n':
  line = ser.readline()
  print(line)

ser.close() 

#loop = asyncio.get_event_loop()
#connection = serial_asyncio.create_serial_connection(loop, GSMModem, MD, baudrate=115200)
#loop.run_until_complete(connection)
#sendSmsTask = loop.create_task(send_sms())
#loop.run_until_complete(sendSmsTask)
#loop.close()

# stty -F $MD 9600 min 100 time 2 -hupcl brkint ignpar -opost -onlcr -isig -icanon -echo

# chat TIMEOUT 10 "" "AT+CMGF=1" "OK" > $MD < $MD
# chat TIMEOUT 10 "" "AT+CMGS=\"$DEST\"" "OK" > $MD < $MD
# chat TIMEOUT 10 "" "$message^Z" "OK" > $MD < $MD
