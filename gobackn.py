import math
import socket
from threading import Thread, Semaphore, Condition, Lock
import random
from struct import pack, unpack

lorem = """Ducimus officia et nobis. Officia quas fuga deleniti. Ab aliquam laboriosam cum enim fugiat neque quaerat.

Incidunt adipisci necessitatibus eos culpa. Et et quo pariatur odio illo dolor deleniti est. Nam sit similique alias. Asperiores exercitationem voluptatem aliquid.

Sint aut quia amet molestiae et. Sint ad necessitatibus consequuntur. Et at a est ut voluptate. Quae omnis amet autem.

Eum aliquam optio inventore culpa voluptatem non ut necessitatibus. Cupiditate nisi rem et voluptatem nam. Ab quod nihil et voluptatem voluptatum reprehenderit est eos. Et explicabo rerum architecto omnis at ipsa ut.

Sunt est laudantium sint et. Velit aliquid voluptas quod. Autem reiciendis accusamus temporibus veritatis quia non vero labore. Quo vel id modi doloribus quas ullam at quod. Enim qui repellendus voluptas sed corporis saepe."""

print_lock = Semaphore()


def my_print(*values):
    print_lock.acquire()
    print(*values)
    print_lock.release()


# is_ack(1 byte) + packet_counter(4 bytes)
HEADER_SIZE = 5
HEADER_FORMAT = "!?I"


def access_chunk(data: bytes, index: int, chunk_size: int) -> bytes:
    start = index * chunk_size
    return data[start:(start+chunk_size)]


class GoBackNSocket:
    def __init__(self):
        # self.window_size = window_size
        # self.timeout = timeout
        # self.sem = Semaphore(self.window_size)
        # self.expected_ack = 0
        # self.last_ack = None
        # self.concat_message = b''
        # self.total_packets = 0
        # self.sem_finalize = Semaphore()
        # self.sem_finalize.acquire()

        self.window_size = 4
        self.timeout = 0.1
        self.current_window = 0  # client
        self.waiting_for = 0  # client
        self.sending_sem = Semaphore(self.window_size)
        self.finalizer = Condition()
        self.successful_finalize = False
        self.expected_packet_id = 0  # server
        self.last_ack = None
        self.final_message = b''

    # When a timeout occurs, return the current_window
    def acquire(self, packet_id):
        success = self.sending_sem.acquire(True, self.timeout)
        if success:
            return packet_id
        else:
            self.handle_timeout()
            self.sending_sem._value = self.window_size
            with self.sending_sem._cond:
                self.sending_sem._cond.notify()
            self.sending_sem.acquire()
            return self.current_window

    def handle_timeout(self):
        my_print("[TIMEOUT] packet", self.current_window,
                 "timed out starting over")
        return self.current_window

    def finalize(self):
        self.successful_finalize = True
        self.finalizer.notify()

    def wait_for_finalize(self):
        self.finalizer.wait(self.timeout)
        return self.successful_finalize

    def receive(self, packet) -> (int, bool):
        (is_ack, id,) = unpack(HEADER_FORMAT, packet[:HEADER_SIZE])

        # my_print("[RECEIVE] Received packet with size",
        #          len(packet), "id", id, "ack", is_ack)

        if is_ack:
            # Client
            if id == self.current_window:  # will ignore what we dont want
                my_print("[CLIENT ACK] ack recieved", id)
                self.current_window += 1
                self.sending_sem.release()  # allow for one more packet to be sent
                with self.finalizer:
                    self.finalizer.notify()
            else:
                pass
                # my_print("ignoring ack", id)
            return (id, False)
        elif id == self.expected_packet_id:  # expected packet id
            # Server
            content = packet[HEADER_SIZE:]
            self.final_message += content

            self.expected_packet_id += 1
            self.last_ack = id

            my_print('[GBN] received:', 'id',
                     id, 'content:', content, "next i want", self.expected_packet_id, "last ack sent was", self.last_ack)

            return (id, True)  # send ack for the received packet
        elif id < self.expected_packet_id:
            return (id, True)  # re-ack already received packet
        else:
            # Server
            # the ack was in wrong order, we missed something
            my_print("[ORDER] received a data packet in the wrong order id",
                     id, "expected", self.expected_packet_id, "resending", self.last_ack)
            if self.last_ack is None:
                return (0, False)
            else:
                return (self.last_ack, True)  # send last ack again


class lossy_udp_socket():
    nBytes = 140

    def __init__(self, conn: GoBackNSocket, loc_port, rem_addr, PLR):
        # conn: handler to be called for received packets with function "receive(packet)"
        # loc_port: local port
        # rem_addr: remote address and port pair
        # PLR: received packets are dropped with probability PLR
        self.conn = conn
        self.STOP = False
        self.PLR = PLR
        self.addr = rem_addr

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self.sock.bind(('', loc_port))

        # spawn server thread
        t = Thread(target=self.recv)
        t.start()

    # interface for sending packets
    def send(self, p):
        offset = self.conn.current_window
        chunk_size = self.nBytes - HEADER_SIZE
        total_packets = math.ceil(len(p) / chunk_size)
        current_packet = offset

        while True:
            current_packet = self.conn.acquire(current_packet)

            packet = access_chunk(p, current_packet - offset, chunk_size)
            if len(packet) == 0:
                with self.conn.finalizer:
                    self.conn.finalizer.wait(self.conn.timeout)
                    # when a packet times out
                    if self.conn.current_window < total_packets + offset:
                        current_packet = self.conn.handle_timeout()
                        continue
                    else:
                        break

            outbound = pack(HEADER_FORMAT, False, current_packet) + packet
            my_print('[SEND] Sending packet with length: ' +
                     str(len(outbound)), "id", current_packet)
            # 1. client sends data in split chunks
            self.sock.sendto(outbound, self.addr)

            # get the next packet
            current_packet += 1

    # interface for ending socket
    def stop(self):
        my_print("content received:", self.conn.final_message.decode())
        self.STOP = True

    # continuously listening for incoming packets
    # filters packets for remote address
    # calls "conn.receive" for received packets
    def recv(self):
        my_print("listening on", self.sock.getsockname())
        while not self.STOP:
            try:
                packet, addr = self.sock.recvfrom(self.nBytes)
                # TEMP if addr == self.addr:
                if True:
                    (_, temp_id) = unpack("!?i", packet[:HEADER_SIZE])
                    if random.random() > self.PLR:
                        # my_print('Received packet with length: '+str(len(packet)), "id", id)
                        id, send_ack = self.conn.receive(packet)
                        if send_ack:
                            # my_print("[SERVER ACK] acking", id)
                            self.sock.sendto(
                                pack(HEADER_FORMAT, True, id), addr)  # send ack
                    else:
                        my_print('[DROP] Dropped packet size',
                                 len(packet), 'id', temp_id)
                else:
                    my_print(
                        'Warning: received packet from remote address'+str(addr))
            except socket.timeout:
                pass


# --------------------------------------------
SERVER_PORT = 4187
IP = '127.0.0.1'
CLIENT_PORT = 0  # get any avaiable port

RELIABILITY = 0.5

client_handler = GoBackNSocket()
client = lossy_udp_socket(client_handler, CLIENT_PORT,
                          (IP, SERVER_PORT), 1 - RELIABILITY)
my_print("client has port", client.sock.getsockname())

server_handler = GoBackNSocket()
server = lossy_udp_socket(server_handler, SERVER_PORT,
                          client.sock.getsockname(), 1 - RELIABILITY)


client.send(b'hello everyone\n')
client.send(b'this is my message 1\n')
client.send(b'this is my message 2\n')
client.send(lorem.encode())
client.stop()
server.stop()
