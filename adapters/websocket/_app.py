"""
websocket - WebSocket client library for Python

Copyright (C) 2010 Hiroki Ohtani(liris)

    This library is free software; you can redistribute it and/or
    modify it under the terms of the GNU Lesser General Public
    License as published by the Free Software Foundation; either
    version 2.1 of the License, or (at your option) any later version.

    This library is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
    Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public
    License along with this library; if not, write to the Free Software
    Foundation, Inc., 51 Franklin Street, Fifth Floor,
    Boston, MA  02110-1335  USA

"""

"""
WebSocketApp provides higher level APIs.
"""
import threading
import time
import traceback
import sys
import select
import six

from ._core import WebSocket, getdefaulttimeout
from ._exceptions import *
from ._logging import *
from websocket._abnf import ABNF
import logging
import logging.handlers

__all__ = ["WebSocketApp"]


class WebSocketApp(object):
    """
    Higher level of APIs are provided.
    The interface is like JavaScript WebSocket object.
    """
    def __init__(self, url, header=[],
                 on_open=None, on_message=None, on_error=None,
                 on_close=None, on_ping=None, on_pong=None,
                 on_cont_message=None,
                 keep_running=True, get_mask_key=None, cookie=None,
                 subprotocols=None):
        """
        url: websocket url.
        header: custom header for websocket handshake.
        on_open: callable object which is called at opening websocket.
          this function has one argument. The arugment is this class object.
        on_message: callbale object which is called when recieved data.
         on_message has 2 arguments.
         The 1st arugment is this class object.
         The passing 2nd arugment is utf-8 string which we get from the server.
        on_error: callable object which is called when we get error.
         on_error has 2 arguments.
         The 1st arugment is this class object.
         The passing 2nd arugment is exception object.
        on_close: callable object which is called when closed the connection.
         this function has one argument. The arugment is this class object.
        on_cont_message: callback object which is called when recieve continued
         frame data.
         on_message has 3 arguments.
         The 1st arugment is this class object.
         The passing 2nd arugment is utf-8 string which we get from the server.
         The 3rd arugment is continue flag. if 0, the data continue
         to next frame data
        keep_running: a boolean flag indicating whether the app's main loop
          should keep running, defaults to True
        get_mask_key: a callable to produce new mask keys,
          see the WebSocket.set_mask_key's docstring for more information
        subprotocols: array of available sub protocols. default is None.
        """
        self.url = url
        self.header = header
        self.cookie = cookie
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.on_ping = on_ping
        self.on_pong = on_pong
        self.on_cont_message = on_cont_message
        self.keep_running = keep_running
        self.get_mask_key = get_mask_key
        self.sock = None
        self.last_ping_tm = 0
        self.subprotocols = subprotocols
        self.logInit()
        
    def logInit(self):
        """define log"""
        self.log = logging.getLogger('WssLog')
        loghdlr = logging.handlers.RotatingFileHandler('WssLog.log',"a", 0, 1)  
        fmt = logging.Formatter("%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        loghdlr.setFormatter(fmt)  
        self.log.addHandler(loghdlr)  
        self.log.setLevel(logging.DEBUG)
        
    def send(self, data, opcode=ABNF.OPCODE_TEXT):
        """
        send message.
        data: message to send. If you set opcode to OPCODE_TEXT,
              data must be utf-8 string or unicode.
        opcode: operation code of data. default is OPCODE_TEXT.
        """

        if not self.sock or self.sock.send(data, opcode) == 0:
            self.log.error(u"wss error {}".format(data))
            raise WebSocketConnectionClosedException("Connection is already closed.")

    def close(self):
        """
        close websocket connection.
        """
        self.keep_running = False
        if self.sock:
            self.sock.close()

    def _send_ping(self, interval, event):
        while not event.wait(interval):
            self.last_ping_tm = time.time()
            if self.sock:
                self.sock.ping()

    def run_forever(self, sockopt=None, sslopt=None,
                    ping_interval=2, ping_timeout=10,
                    http_proxy_host=None, http_proxy_port=None,
                    http_no_proxy=None, http_proxy_auth=None,
                    skip_utf8_validation=False,
                    host=None, origin=None):
        """
        run event loop for WebSocket framework.
        This loop is infinite loop and is alive during websocket is available.
        sockopt: values for socket.setsockopt.
            sockopt must be tuple
            and each element is argument of sock.setscokopt.
        sslopt: ssl socket optional dict.
        ping_interval: automatically send "ping" command
            every specified period(second)
            if set to 0, not send automatically.
        ping_timeout: timeout(second) if the pong message is not recieved.
        http_proxy_host: http proxy host name.
        http_proxy_port: http proxy port. If not set, set to 80.
        http_no_proxy: host names, which doesn't use proxy.
        skip_utf8_validation: skip utf8 validation.
        host: update host header.
        origin: update origin header.
        """

        if not ping_timeout or ping_timeout <= 0:
            ping_timeout = None
        if sockopt is None:
            sockopt = []
        if sslopt is None:
            sslopt = {}
        if self.sock:
            raise WebSocketException("socket is already opened")
        thread = None
        close_frame = None

        try:
            self.sock = WebSocket(self.get_mask_key,
                sockopt=sockopt, sslopt=sslopt,
                fire_cont_frame=self.on_cont_message and True or False,
                skip_utf8_validation=skip_utf8_validation)
            self.sock.settimeout(getdefaulttimeout())
            self.sock.connect(self.url, header=self.header, cookie=self.cookie,
                http_proxy_host=http_proxy_host,
                http_proxy_port=http_proxy_port,
                http_no_proxy=http_no_proxy, http_proxy_auth=http_proxy_auth,
                subprotocols=self.subprotocols,
                host=host, origin=origin)
            self._callback(self.on_open)

            if ping_interval:
                event = threading.Event()
                thread = threading.Thread(target=self._send_ping, args=(ping_interval, event))
                thread.setDaemon(True)
                thread.start()

            while self.sock.connected:
                r, w, e = select.select((self.sock.sock, ), (), (), ping_timeout)
                if not self.keep_running:
                    break
                if ping_timeout and self.last_ping_tm and time.time() - self.last_ping_tm > ping_timeout:
                    self.last_ping_tm = 0
                    raise WebSocketTimeoutException("ping timed out")

                if r:
                    op_code, frame = self.sock.recv_data_frame(True)
                    if op_code == ABNF.OPCODE_CLOSE:
                        close_frame = frame
                        break
                    elif op_code == ABNF.OPCODE_PING:
                        self._callback(self.on_ping, frame.data)
                    elif op_code == ABNF.OPCODE_PONG:
                        self._callback(self.on_pong, frame.data)
                    elif op_code == ABNF.OPCODE_CONT and self.on_cont_message:
                        self._callback(self.on_cont_message, frame.data, frame.fin)
                    else:
                        data = frame.data
                        if six.PY3 and frame.opcode == ABNF.OPCODE_TEXT:
                            data = data.decode("utf-8")
                        self._callback(self.on_message, data)
        except Exception as e:
            self._callback(self.on_error, e)
        finally:
            if thread:
                event.set()
                thread.join()
                self.keep_running = False
            self.sock.close()
            self._callback(self.on_close,
                *self._get_close_args(close_frame.data if close_frame else None))
            self.sock = None

    def _get_close_args(self, data):
        """ this functions extracts the code, reason from the close body
        if they exists, and if the self.on_close except three arguments """
        import inspect
        # if the on_close callback is "old", just return empty list
        if not self.on_close or len(inspect.getargspec(self.on_close).args) != 3:
            return []

        if data and len(data) >= 2:
            code = 256*six.byte2int(data[0:1]) + six.byte2int(data[1:2])
            reason = data[2:].decode('utf-8')
            return [code, reason]

        return [None, None]

    def _callback(self, callback, *args):
        if callback:
            try:
                callback(self, *args)
            except Exception as e:
                error(e)
                if isEnabledForDebug():
                    _, _, tb = sys.exc_info()
                    traceback.print_tb(tb)
