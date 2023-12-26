import io
import socket
from threading import Thread, Semaphore
import random
import time
from struct import pack, unpack

lorem = """Ducimus officia et nobis. Officia quas fuga deleniti. Ab aliquam laboriosam cum enim fugiat neque quaerat.

Incidunt adipisci necessitatibus eos culpa. Et et quo pariatur odio illo dolor deleniti est. Nam sit similique alias. Asperiores exercitationem voluptatem aliquid.

Sint aut quia amet molestiae et. Sint ad necessitatibus consequuntur. Et at a est ut voluptate. Quae omnis amet autem.

Eum aliquam optio inventore culpa voluptatem non ut necessitatibus. Cupiditate nisi rem et voluptatem nam. Ab quod nihil et voluptatem voluptatum reprehenderit est eos. Et explicabo rerum architecto omnis at ipsa ut.

Sunt est laudantium sint et. Velit aliquid voluptas quod. Autem reiciendis accusamus temporibus veritatis quia non vero labore. Quo vel id modi doloribus quas ullam at quod. Enim qui repellendus voluptas sed corporis saepe."""


def access_chunk(data: bytes, index: int, chunk_size: int) -> bytes:
    start = index * chunk_size
    return data[start:(start+chunk_size)]


class GoBackNSocket:
    def __init__(self, window_size = 4, timeout = 1.0):
        self.last_sent_packet_time = time.localtime()
        self.window_size = window_size
        self.timeout = timeout
        self.sem = Semaphore(self.window_size)
        self.expected_ack = 0
        self.concat_message = b''

    def receive(self, packet) -> int:
        (packet_counter,) = unpack("!i", packet[:4])
        if packet_counter < 0 and -packet_counter == self.expected_ack:
            print("got a good ack", self.expected_ack)
            self.expected_ack += 1
            # we have gotten a valid ack
        else:
            content = packet[4:]
            print('GBN receive:', 'counter:', packet_counter, 'content:', content)
            self.concat_message += content

            return packet_counter

    def sending(self):
        self.sem.acquire()


class lossy_udp_socket():
    nBytes = 15

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
        chunk_size = self.nBytes - 4
        packet_counter = 0

        while True:
            packet = access_chunk(p, packet_counter, chunk_size)
            if len(packet) < 1:
                break; # jump out if we have nothing left

            self.conn.sending()

            print('Sending packet with length: ' + str(len(packet)))

            outbound = pack("!i", packet_counter) + packet
            self.sock.sendto(outbound, self.addr)

            # get the next packet
            packet_counter += 1

    # interface for ending socket
    def stop(self):
        self.STOP = True

    # continuously listening for incoming packets
    # filters packets for remote address
    # calls "conn.receive" for received packets
    def recv(self):
        print("listening on", self.sock.getsockname())
        while not self.STOP:
            try:
                packet, addr = self.sock.recvfrom(self.nBytes)
                # TODO temp if addr == self.addr:
                if True:
                    if random.random() > self.PLR:
                        print('Received packet with length: '+str(len(packet)))
                        id = self.conn.receive(packet)
                        print("acking", id)
                        self.sock.sendto(pack("!i", -id), addr) # send ack
                    else:
                        print('Dropped packet with length: '+str(len(packet)))
                else:
                    print('Warning: received packet from remote address'+str(addr))
            except socket.timeout:
                pass


# --------------------------------------------
SERVER_PORT = 4187
IP = '127.0.0.1'
CLIENT_PORT = 0  # get any avaiable port

server_handler = GoBackNSocket()
server = lossy_udp_socket(server_handler, SERVER_PORT, (), 0.0)
t = Thread(target=server.recv)
t.start()

handler = GoBackNSocket()
sock = lossy_udp_socket(handler, CLIENT_PORT, (IP, SERVER_PORT), 0.0)
print("client has port", sock.sock.getsockname())

sock.send(lorem.encode())
server.stop()
sock.stop()
