"""Loopback test: pymodbus client (playing the PLC) against the sim's server."""
import sys
import time

sys.path.insert(0, ".")

from pymodbus.client import ModbusTcpClient

from conveyor_sim.iomap import N_IN, N_OUT
from conveyor_sim.plc import ModbusServerLink

PORT = 15020


def main():
    link = ModbusServerLink("127.0.0.1", PORT)
    time.sleep(0.2)
    c = ModbusTcpClient("127.0.0.1", port=PORT)
    assert c.connect(), "client failed to connect"

    # sim publishes sensors -> PLC reads discrete inputs (FC02)
    sensors = [True, False, True, False, False, True,
               False, False, True, True, False, True][:N_IN]
    assert len(sensors) == N_IN
    link.push_inputs(sensors)
    rr = c.read_discrete_inputs(0, count=N_IN)
    assert not rr.isError(), rr
    assert [bool(b) for b in rr.bits[:N_IN]] == sensors, rr.bits[:N_IN]
    print(f"  FC02 read discrete inputs OK (zero-based addr 0..{N_IN - 1})")

    # sensors also packed into input register 0 (FC04)
    rr = c.read_input_registers(0, count=1)
    expect = sum(1 << i for i, b in enumerate(sensors) if b)
    assert rr.registers[0] == expect, (rr.registers, expect)
    print("  FC04 input register 0 packed word OK")

    # PLC writes coils -> sim actuators (FC15 multiple)
    cmds = [False, True, False, False, True, False,
            False, True, True, True, False, True][:N_OUT]
    assert len(cmds) == N_OUT
    rr = c.write_coils(0, list(cmds))   # copy: pymodbus pads the list in place
    assert not rr.isError(), rr
    assert link.pull_outputs() == cmds, link.pull_outputs()
    print("  FC15 write multiple coils OK")

    # FC05 single coil
    rr = c.write_coil(0, True)
    assert not rr.isError(), rr
    assert link.pull_outputs()[0] is True
    print("  FC05 write single coil OK")

    # FC01 read back coils
    rr = c.read_coils(0, count=N_OUT)
    assert [bool(b) for b in rr.bits[:N_OUT]] == link.pull_outputs()
    print("  FC01 read coils OK")

    # FC06 packed word in holding register 0 mirrors to the coils
    rr = c.write_register(0, 0b101000001)
    assert not rr.isError(), rr
    outs = link.pull_outputs()
    expect = [bool(0b101000001 >> i & 1) for i in range(N_OUT)]
    assert outs == expect, outs
    rr = c.read_holding_registers(0, count=1)
    assert rr.registers[0] == 0b101000001
    print("  FC06/FC03 holding register 0 mirror OK")

    # out-of-range -> modbus exception, connection stays usable
    rr = c.read_coils(60, count=10)
    assert rr.isError(), "expected illegal-address exception"
    rr = c.read_coils(0, count=N_OUT)
    assert not rr.isError(), "connection should survive an exception response"
    print("  illegal address exception OK")

    c.close()
    link.close()
    print("all modbus tests passed")


if __name__ == "__main__":
    main()
