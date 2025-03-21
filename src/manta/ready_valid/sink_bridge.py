from amaranth import *
from manta.utils import InternalBus


class ReadyValidSinkBridge(Elaboratable):
    """
    A module for bridging Manta's internal bus to a UART-like stream of bytes,
    using a ready/valid handshake.
    """

    def __init__(self):
        # Manta internal bus input

        self.bus_i = Signal(InternalBus())

        # Top-Level Ports
        # self.data_i = Signal(16)
        # self.rw_i = Signal()
        # self.valid_i = Signal()

        self.data_o = Signal(8)
        self.valid_o = Signal()
        self.ready_i = Signal()

        # Internal Signals
        self._buffer = Signal(16)
        self._count = Signal(4)
        self._busy = Signal(1)
        self._to_ascii_hex = Signal(8)
        self._nibble = Signal(4)

    def elaborate(self, platform):
        m = Module()

        # Set valid_o when _busy and still sending bytes
        m.d.comb += self.valid_o.eq(self._busy & (self._count < 7))

        with m.If(~self._busy):
            with m.If(self.bus_i.valid & ~self.bus_i.rw):
                m.d.sync += self._busy.eq(1)
                m.d.sync += self._buffer.eq(self.bus_i.data)
                m.d.sync += self._count.eq(0)

        with m.Elif(self.ready_i):  # Move to the next byte only when ready
            m.d.sync += self._count.eq(self._count + 1)

            # Message fully transmitted
            with m.If(self._count >= 6):
                m.d.sync += self._busy.eq(0)

            # Check if a new message is available immediately
            with m.Elif(self.bus_i.valid & ~self.bus_i.rw & (self._count >= 6)):
                m.d.sync += self._buffer.eq(self.bus_i.data)
                m.d.sync += self._count.eq(0)
                m.d.sync += self._busy.eq(1)

        # Convert 4-bit nibbles to ASCII hex
        with m.If(self._nibble < 10):
            m.d.comb += self._to_ascii_hex.eq(self._nibble + 0x30)  # '0'-'9'
        with m.Else():
            m.d.comb += self._to_ascii_hex.eq(self._nibble + 0x41 - 10)  # 'A'-'F'

        # Output sequence per count
        with m.Switch(self._count):
            with m.Case(0):
                m.d.comb += self._nibble.eq(0), self.data_o.eq(ord("D"))
            with m.Case(1):
                m.d.comb += self._nibble.eq(self._buffer[12:16]), self.data_o.eq(
                    self._to_ascii_hex
                )
            with m.Case(2):
                m.d.comb += self._nibble.eq(self._buffer[8:12]), self.data_o.eq(
                    self._to_ascii_hex
                )
            with m.Case(3):
                m.d.comb += self._nibble.eq(self._buffer[4:8]), self.data_o.eq(
                    self._to_ascii_hex
                )
            with m.Case(4):
                m.d.comb += self._nibble.eq(self._buffer[0:4]), self.data_o.eq(
                    self._to_ascii_hex
                )
            with m.Case(5):
                m.d.comb += self._nibble.eq(0), self.data_o.eq(ord("\r"))
            with m.Case(6):
                m.d.comb += self._nibble.eq(0), self.data_o.eq(ord("\n"))
            with m.Default():
                m.d.comb += self._nibble.eq(0), self.data_o.eq(0)

        return m
