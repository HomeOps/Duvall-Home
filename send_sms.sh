#!/bin/bash

DEST=$1
MD="/dev/ttyUSB2"

if [ -z "$2" ]
then
    read message
else
    message="$2"
fi

echo "Sending $message to $DEST"

apk add ppp

stty -F $MD 9600 min 100 time 2 -hupcl brkint ignpar -opost -onlcr -isig -icanon -echo

chat TIMEOUT 10 "" "AT+CMGF=1" "OK" > $MD < $MD
chat TIMEOUT 10 "" "AT+CMGS=\"$DEST\"" "OK" > $MD < $MD
chat TIMEOUT 10 "" "$message^Z" "OK" > $MD < $MD
