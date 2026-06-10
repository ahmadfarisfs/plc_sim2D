"""PLC connectivity over Modbus TCP.

Two modes:

* ModbusServerLink (default): the sim is the Modbus TCP *server* (slave).
  Program the PLC as Modbus TCP client to
    - read sensors:    discrete inputs 0..8 (FC02)  [also packed in input reg 0, FC04]
    - write actuators: coils 0..8 (FC05/FC15)       [or packed word in holding reg 0, FC06]
  The server is implemented here directly (no pymodbus server dependency):
  addressing is strictly zero-based.

* ModbusClientLink: the sim is the Modbus TCP *client*, for PLCs that expose
  a built-in Modbus server. Sensor bits are written to PLC coils at
  --in-base, actuator commands are read from PLC coils at --out-base.

* McProtocolLink: Mitsubishi MC protocol (pymcprotocol). The PLC program runs
  with G_IsSimulated=TRUE so its inputs come from M devices: the sim batch-
  writes sensor bits to M200..M211 and batch-reads commands from Y0..Y16.
"""
import socketserver
import struct
import threading
import time

from .iomap import MC_M_BASE, MC_Y_DEVICES, N_IN, N_OUT


def _pack_bits(bits):
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def _unpack_bits(data, count):
    return [bool(data[i // 8] >> (i % 8) & 1) for i in range(count)]


def _bits_to_word(bits):
    return sum(1 << i for i, b in enumerate(bits[:16]) if b)


class ManualLink:
    """No PLC: outputs come only from the keyboard."""

    status = "no PLC (manual control)"

    def push_inputs(self, bits):
        pass

    def pull_outputs(self):
        return None


class _Store:
    """Zero-based Modbus data model, shared between sim loop and server threads."""

    N_BITS = 64
    N_REGS = 16

    def __init__(self):
        self.lock = threading.Lock()
        self.discrete = [False] * self.N_BITS
        self.coils = [False] * self.N_BITS
        self.input_regs = [0] * self.N_REGS
        self.holding = [0] * self.N_REGS

    # sim side
    def set_inputs(self, bits):
        with self.lock:
            self.discrete[:len(bits)] = bits
            self.input_regs[0] = _bits_to_word(bits)

    def get_outputs(self):
        with self.lock:
            return self.coils[:N_OUT]

    # server side
    def read(self, table, addr, count):
        with self.lock:
            return list(table[addr:addr + count])

    def write_coils(self, addr, values):
        with self.lock:
            self.coils[addr:addr + len(values)] = values
            self.holding[0] = _bits_to_word(self.coils)

    def write_holding(self, addr, values):
        with self.lock:
            self.holding[addr:addr + len(values)] = values
            if addr == 0:    # holding reg 0 mirrors the coils, bit 0 = coil 0
                for i in range(16):
                    self.coils[i] = bool(self.holding[0] >> i & 1)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        link = self.server.link
        with link.store.lock:
            link.clients += 1
        try:
            while True:
                hdr = self._recv(7)
                if hdr is None:
                    return
                tid, _pid, length, uid = struct.unpack(">HHHB", hdr)
                pdu = self._recv(length - 1)
                if pdu is None or not pdu:
                    return
                resp = self._respond(pdu)
                mbap = struct.pack(">HHHB", tid, 0, len(resp) + 1, uid)
                self.request.sendall(mbap + resp)
        except (ConnectionError, OSError):
            pass
        finally:
            with link.store.lock:
                link.clients -= 1

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _respond(self, pdu):
        store = self.server.link.store
        fc = pdu[0]
        try:
            if fc in (1, 2):                       # read coils / discrete inputs
                addr, count = struct.unpack(">HH", pdu[1:5])
                if not 1 <= count <= 2000 or addr + count > store.N_BITS:
                    return bytes([fc | 0x80, 2])
                table = store.coils if fc == 1 else store.discrete
                data = _pack_bits(store.read(table, addr, count))
                return bytes([fc, len(data)]) + data
            if fc in (3, 4):                       # read holding / input registers
                addr, count = struct.unpack(">HH", pdu[1:5])
                if not 1 <= count <= 125 or addr + count > store.N_REGS:
                    return bytes([fc | 0x80, 2])
                table = store.holding if fc == 3 else store.input_regs
                regs = store.read(table, addr, count)
                return bytes([fc, 2 * len(regs)]) + \
                    b"".join(struct.pack(">H", r & 0xFFFF) for r in regs)
            if fc == 5:                            # write single coil
                addr, val = struct.unpack(">HH", pdu[1:5])
                if val not in (0x0000, 0xFF00):
                    return bytes([fc | 0x80, 3])
                if addr >= store.N_BITS:
                    return bytes([fc | 0x80, 2])
                store.write_coils(addr, [val == 0xFF00])
                return pdu[:5]
            if fc == 6:                            # write single register
                addr, val = struct.unpack(">HH", pdu[1:5])
                if addr >= store.N_REGS:
                    return bytes([fc | 0x80, 2])
                store.write_holding(addr, [val])
                return pdu[:5]
            if fc == 15:                           # write multiple coils
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                if addr + count > store.N_BITS or nbytes != (count + 7) // 8:
                    return bytes([fc | 0x80, 2])
                store.write_coils(addr, _unpack_bits(pdu[6:6 + nbytes], count))
                return bytes([fc]) + pdu[1:5]
            if fc == 16:                           # write multiple registers
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                if addr + count > store.N_REGS or nbytes != 2 * count:
                    return bytes([fc | 0x80, 2])
                vals = [struct.unpack(">H", pdu[6 + 2 * i:8 + 2 * i])[0]
                        for i in range(count)]
                store.write_holding(addr, vals)
                return bytes([fc]) + pdu[1:5]
            return bytes([fc | 0x80, 1])           # illegal function
        except (struct.error, IndexError):
            return bytes([fc | 0x80, 3])           # illegal data value


class ModbusServerLink:
    """Sim acts as Modbus TCP server; the real PLC connects as client."""

    def __init__(self, host="0.0.0.0", port=5020):
        self.store = _Store()
        self.clients = 0
        srv = socketserver.ThreadingTCPServer((host, port), _Handler,
                                              bind_and_activate=False)
        srv.allow_reuse_address = True
        srv.daemon_threads = True
        srv.server_bind()
        srv.server_activate()
        srv.link = self
        self._srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self._desc = f"Modbus server on {host}:{port}"

    def push_inputs(self, bits):
        self.store.set_inputs(bits)

    def pull_outputs(self):
        return self.store.get_outputs()

    @property
    def status(self):
        return f"{self._desc} | PLC connections: {self.clients}"

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()


class ModbusClientLink:
    """Sim acts as Modbus TCP client; the PLC runs its built-in Modbus server."""

    def __init__(self, host, port=502, device_id=1, in_base=0, out_base=16):
        from pymodbus.client import ModbusTcpClient
        self._client = ModbusTcpClient(host, port=port)
        self._device_id = device_id
        self._in_base = in_base
        self._out_base = out_base
        self._lock = threading.Lock()
        self._in_bits = [False] * N_IN
        self._out_bits = [False] * N_OUT
        self.ok = False
        self._desc = f"Modbus client -> {host}:{port} " \
                     f"(sensors @coil {in_base}+, cmds @coil {out_base}+)"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                if not self._client.connected and not self._client.connect():
                    self.ok = False
                    time.sleep(1.0)
                    continue
                with self._lock:
                    in_bits = list(self._in_bits)
                self._client.write_coils(self._in_base, in_bits,
                                         device_id=self._device_id)
                rr = self._client.read_coils(self._out_base, count=N_OUT,
                                             device_id=self._device_id)
                if rr.isError():
                    self.ok = False
                else:
                    with self._lock:
                        self._out_bits = [bool(b) for b in rr.bits[:N_OUT]]
                    self.ok = True
                time.sleep(0.02)
            except Exception:
                self.ok = False
                self._client.close()
                time.sleep(1.0)

    def push_inputs(self, bits):
        with self._lock:
            self._in_bits = list(bits)

    def pull_outputs(self):
        with self._lock:
            return list(self._out_bits)

    @property
    def status(self):
        return f"{self._desc} | {'OK' if self.ok else 'reconnecting...'}"


class McProtocolLink:
    """Mitsubishi MC protocol client (3E frame) via pymcprotocol.

    The PLC must run with G_IsSimulated=TRUE: sensors are written to
    M<m_base>.. in iomap order and commands are read from the Y devices in
    MC_Y_DEVICES. Y numbering is octal by default (FX style: Y7 -> Y10);
    pass y_radix=16 for hex-numbered (Q/iQ) outputs.
    """

    def __init__(self, host, port=5007, plctype="Q", m_base=MC_M_BASE,
                 y_radix=8):
        import pymcprotocol
        self._mc = pymcprotocol.Type3E(plctype=plctype)
        self._host, self._port = host, port
        self._head_m = f"M{m_base}"
        y_nums = [int(d[1:], y_radix) for d in MC_Y_DEVICES]
        self._y_index = [n - y_nums[0] for n in y_nums]
        self._y_count = self._y_index[-1] + 1
        self._y_head = MC_Y_DEVICES[0]
        self._lock = threading.Lock()
        self._in_bits = [False] * N_IN
        self._out_bits = [False] * N_OUT
        self.ok = False
        self._connected = False
        self._desc = f"MC protocol -> {host}:{port} " \
                     f"(sensors @{self._head_m}+, cmds @Y, plctype {plctype})"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                if not self._connected:
                    self._mc.connect(self._host, self._port)
                    self._connected = True
                with self._lock:
                    in_bits = [int(b) for b in self._in_bits]
                self._mc.batchwrite_bitunits(self._head_m, in_bits)
                ys = self._mc.batchread_bitunits(self._y_head, self._y_count)
                with self._lock:
                    self._out_bits = [bool(ys[i]) for i in self._y_index]
                self.ok = True
                time.sleep(0.02)
            except Exception:
                self.ok = False
                self._connected = False
                try:
                    self._mc.close()
                except Exception:
                    pass
                time.sleep(1.0)

    def push_inputs(self, bits):
        with self._lock:
            self._in_bits = list(bits)

    def pull_outputs(self):
        with self._lock:
            return list(self._out_bits)

    @property
    def status(self):
        return f"{self._desc} | {'OK' if self.ok else 'reconnecting...'}"


def make_link(args):
    if args.mode == "server":
        return ModbusServerLink(args.host or "0.0.0.0", args.port or 5020)
    if args.mode == "client":
        if not args.host:
            raise SystemExit("--mode client requires --host <PLC address>")
        return ModbusClientLink(args.host, args.port or 502, args.device_id,
                                args.in_base, args.out_base)
    if args.mode == "mc":
        if not args.host:
            raise SystemExit("--mode mc requires --host <PLC address>")
        return McProtocolLink(args.host, args.port or 5007, args.plctype,
                              args.m_base, 16 if args.y_radix == "hex" else 8)
    return ManualLink()
