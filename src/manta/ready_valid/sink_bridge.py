from amaranth import *
from manta.utils import InternalBus

BYTES_IN_RESPONSE = 7


class ReadyValidSinkBridge(Elaboratable):
    """
    A module for bridging Manta's internal bus to a UART-like stream of bytes,
    using a ready/valid handshake.
    """

    def __init__(self):
        # Top-Level Ports
        self.bus_i = Signal(InternalBus())

        self.ready_i = Signal()

        self.data_o = Signal(8)
        self.valid_o = Signal()

        # Internal Signals
        self._buffer = Array(Signal(8) for _ in range(4))  # Store only hex digits
        self._count = Signal(4)
        self._busy = Signal(1)
        self._to_ascii_hex = Signal(8)
        self._nibble = Signal(4)

    def _nibble_to_ascii(self, nibble):
        return Mux(nibble < 10, nibble + 0x30, nibble + 0x41 - 10)

    def elaborate(self, platform):
        m = Module()

        # Set valid_o when busy and still sending bytes
        m.d.comb += self.valid_o.eq(self._busy & (self._count < BYTES_IN_RESPONSE))

        with m.If(self.bus_i.valid & ~self.bus_i.rw & ~self._busy):
            # Store the variable bytes only
            m.d.sync += [
                self._buffer[0].eq(self._nibble_to_ascii(self.bus_i.data[12:16])),
                self._buffer[1].eq(self._nibble_to_ascii(self.bus_i.data[8:12])),
                self._buffer[2].eq(self._nibble_to_ascii(self.bus_i.data[4:8])),
                self._buffer[3].eq(self._nibble_to_ascii(self.bus_i.data[0:4])),
                self._count.eq(0),
                self._busy.eq(1),
            ]

        with m.Elif(self.ready_i & self._busy):  # Move to the next byte only when ready
            m.d.sync += self._count.eq(self._count + 1)

            # If all bytes have been transmitted, stop
            with m.If(self._count == BYTES_IN_RESPONSE - 1):
                m.d.sync += self._busy.eq(0)

        # Output the current byte based on count
        with m.Switch(self._count):
            with m.Case(0):
                m.d.comb += self.data_o.eq(ord("D"))
            with m.Case(1, 2, 3, 4):
                m.d.comb += self.data_o.eq(self._buffer[self._count - 1])
            with m.Case(5):
                m.d.comb += self.data_o.eq(ord("\r"))
            with m.Case(6):
                m.d.comb += self.data_o.eq(ord("\n"))
            with m.Default():
                m.d.comb += self.data_o.eq(0)

        return m
