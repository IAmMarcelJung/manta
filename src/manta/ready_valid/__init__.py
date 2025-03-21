from amaranth import *
from serial import Serial

from manta.ready_valid.sink_bridge import ReadyValidSinkBridge
from manta.ready_valid.source_bridge import ReadyValidSourceBridge
from manta.utils import *


class ReadyValidInterface(Elaboratable):
    """
    A synthesizable module to connect manta to an 8-bit ready/valid interface.
    """

    def __init__(self, port, clock_freq, stall_interval=16, chunk_size=256):
        """
        This function is the main mechanism for configuring a valid/ready Interface
        in an Amaranth-native design.

        Args:
            port (str): The name of the serial port on the host machine that's
                connected to the FPGA. Depending on your platform, this could
                be `/dev/ttyACM0`, or `COMX`.

            clock_freq (float | int): The frequency of the clock provided to
                this module, in Hertz (Hz).


            stall_interval (Optional[int]): The number of read requests to send
                before sending a stall byte. This prevents packets from being
                dropped if the FPGA's baudrate is less than the USB-Serial
                adapter's baudrate. This is usually caused by a mismatch
                between the clock frequency of the USB-Serial adapter and the
                FPGA fabric. See issue #18 on GitHub. Reduce this if Manta
                reports that bytes are being dropped.

            chunk_size (Optional[int]): The number of read requests to send at
                a time. Since the FPGA responds to read requests almost
                instantly, sending them in batches prevents the host machine's
                input buffer from overflowing. Reduce this if Manta reports
                that bytes are being dropped, and decreasing `stall_interval`
                did not work.

        Raises:
            ValueError: The baudrate is not achievable with the clock frequency
                provided, or the clock frequency or baudrate is invalid.

        """

        self._port = port
        # not needed for the direct USB interface, set to some default value
        self._baudrate = 115200
        self._clock_freq = clock_freq
        self._chunk_size = chunk_size
        self._stall_interval = stall_interval
        self._check_config()

        # Top-Level Ports
        self.receive_data = Signal(8)
        self.receive_ready = Signal()
        self.receive_valid = Signal()

        self.transmit_data = Signal(8)
        self.transmit_ready = Signal()
        self.transmit_valid = Signal()

        self.bus_o = Signal(InternalBus())
        self.bus_i = Signal(InternalBus())

    @classmethod
    def from_config(cls, config):
        port = config.get("port")
        clock_freq = config.get("clock_freq")

        # Warn if unrecognized options have been given
        recognized_options = [
            "port",
            "chunk_size",
            "clock_freq",
            "stall_interval",
        ]
        for option in config:
            if option not in recognized_options:
                warn(
                    f"Ignoring unrecognized option '{option}' in USB interface config."
                )

        return cls(**config)

    def to_config(self):
        return {
            "port": self._port,
            "stall_interval": self._stall_interval,
            "chunk_size": self._chunk_size,
            "clock_freq": self._clock_freq,
        }

    def _check_config(self):
        # Ensure a serial port has been given
        if self._port is None:
            raise ValueError("No serial port provided to USB interface.")
        # Ensure clock frequency is provided and positive
        if self._clock_freq is None:
            raise ValueError("No clock frequency provided to USB interface.")

        if self._clock_freq <= 0:
            raise ValueError("Non-positive clock frequency provided to USB interface.")

    def _get_serial_device(self):
        """
        Return an open PySerial serial device if one exists, otherwise, open
        one and return it.
        """

        # Check if we've already opened a device
        if hasattr(self, "_serial_device"):
            return self._serial_device

        self._serial_device = Serial(self._port, self._baudrate, timeout=1)
        return self._serial_device

    def get_top_level_ports(self):
        """
        Return the Amaranth signals that should be included as ports in the
        top-level Manta module.
        """
        return [
            self.receive_data,
            self.receive_ready,
            self.receive_valid,
            self.transmit_data,
            self.transmit_ready,
            self.transmit_valid,
        ]

    @property
    def clock_freq(self):
        return self._clk_freq

    def read(self, addrs):
        """
        Read the data stored in a set of address on Manta's internal memory.
        Addresses must be specified as either integers or a list of integers.
        """

        # Handle a single integer address
        if isinstance(addrs, int):
            return self.read([addrs])[0]

        # Make sure all list elements are integers
        if not all(isinstance(a, int) for a in addrs):
            raise TypeError("Read address must be an integer or list of integers.")

        # Send read requests in chunks, and read bytes after each.
        # The input buffer exposed by the OS on most hosts isn't terribly deep,
        # so sending in chunks (instead of all at once) prevents the OS's input
        # buffer from overflowing and dropping bytes, as the FPGA will send
        # responses instantly after it's received a request.

        ser = self._get_serial_device()
        addr_chunks = split_into_chunks(addrs, self._chunk_size)
        data = []

        for addr_chunk in addr_chunks:
            # Encode addrs into read requests
            bytes_out = "".join([f"R{a:04X}\r\n" for a in addr_chunk])

            # Add a \n after every N packets, see:
            # https://github.com/fischermoseley/manta/issues/18
            bytes_out = split_into_chunks(bytes_out, 7 * self._stall_interval)
            bytes_out = "\n".join(bytes_out)

            ser.write(bytes_out.encode("ascii"))

            # Read responses have the same length as read requests
            bytes_expected = 7 * len(addr_chunk)
            bytes_in = ser.read(bytes_expected)
            print(bytes_in)

            if len(bytes_in) != bytes_expected:
                raise ValueError(
                    f"Only got {len(bytes_in)} out of {bytes_expected} bytes."
                )

            # Split received bytes into individual responses and decode
            responses = split_into_chunks(bytes_in, 7)
            data_chunk = [self._decode_read_response(r) for r in responses]
            data += data_chunk

        return data

    def write(self, addrs, data):
        """
        Write the provided data into the provided addresses in Manta's internal
        memory. Addresses and data must be specified as either integers or a
        list of integers.
        """

        # Handle a single integer address and data
        if isinstance(addrs, int) and isinstance(data, int):
            return self.write([addrs], [data])

        # Make sure address and data are all integers
        if not isinstance(addrs, list) or not isinstance(data, list):
            raise TypeError(
                "Write addresses and data must be an integer or list of integers."
            )

        if not all(isinstance(a, int) for a in addrs):
            raise TypeError("Write addresses must be all be integers.")

        if not all(isinstance(d, int) for d in data):
            raise TypeError("Write data must all be integers.")

        # Since the FPGA doesn't issue any responses to write requests, we
        # the host's input buffer isn't written to, and we don't need to
        # send the data as chunks as the to avoid overflowing the input buffer.

        # Encode addrs and data into write requests
        bytes_out = "".join([f"W{a:04X}{d:04X}\r\n" for a, d in zip(addrs, data)])
        ser = self._get_serial_device()
        ser.write(bytes_out.encode("ascii"))

    def _decode_read_response(self, response_bytes):
        """
        Check that read response is formatted properly, and return the encoded
        data if so.
        """

        # Make sure response is not empty
        if response_bytes is None:
            raise ValueError("Unable to decode read response - no bytes received.")

        # Make sure response is properly encoded
        response_ascii = response_bytes.decode("ascii")

        if len(response_ascii) != 7:
            raise ValueError(
                "Unable to decode read response - wrong number of bytes received."
            )

        if response_ascii[0] != "D":
            raise ValueError("Unable to decode read response - incorrect preamble.")

        for i in range(1, 5):
            if response_ascii[i] not in "0123456789ABCDEF":
                raise ValueError("Unable to decode read response - invalid data byte.")

        if response_ascii[5] != "\r":
            raise ValueError("Unable to decode read response - incorrect EOL.")

        if response_ascii[6] != "\n":
            raise ValueError("Unable to decode read response - incorrect EOL.")

        return int(response_ascii[1:5], 16)

    def elaborate(self, platform):
        m = Module()

        m.submodules.source_bridge = source_bridge = ReadyValidSourceBridge()
        m.submodules.sink_bridge = sink_bridge = ReadyValidSinkBridge()

        m.d.comb += [
            # Ready/valid -> Internal Bus
            source_bridge.data_i.eq(self.receive_data),
            source_bridge.valid_i.eq(self.receive_valid),
            self.receive_ready.eq(source_bridge.ready_o),
            # Internal Bus  -> Ready/valid
            self.transmit_data.eq(sink_bridge.data_o),
            self.transmit_valid.eq(sink_bridge.valid_o),
            sink_bridge.ready_i.eq(self.transmit_ready),
            # Bus connections
            sink_bridge.bus_i.eq(self.bus_i),
            self.bus_o.eq(source_bridge.bus_o),
        ]
        return m
