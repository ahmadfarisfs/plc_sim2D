import tkinter as tk
from tkinter import ttk
from pymodbus.client import ModbusTcpClient

# ==========================================
# FX5U-64MT/ES MODBUS SETTINGS
# ==========================================

PLC_IP = "192.168.3.10"
# PLC_IP = "127.0.0.1"
PLC_PORT = 502
DEVICE_ID = 1

# GX Works3 Modbus Allocation
# X0 -> Input 13312
# Y0 -> Coil 13056

INPUT_START = 13312
INPUT_COUNT = 32

OUTPUT_START = 13056
OUTPUT_COUNT = 32

POLL_MS = 500

# ==========================================
# PERIPHERAL DEVICE MAP (GX Works3 labels)
# ==========================================
# Sections shown on the "Peripherals" tab.
# Each entry: (Label Name, Device "X.."/"Y..")

PERIPHERALS = {

    "Lifter A": [
        ("O_LftA_LiftUp",   "Y1"),
        ("O_LftA_LiftDown", "Y0"),
        ("O_LftA_BeltFwd",  "Y3"),
        ("O_LftA_BeltRev",  "Y2"),
        ("I_LftA_BeltEnd",  "X3"),
        ("I_LftA_UpLimit",  "X0"),
        ("I_LftA_DnLimit",  "X1"),
        ("I_LftA_EmergencyStop", "X5"),
    ],

    "Lifter B": [
        ("O_LftB_LiftUp",    "Y5"),
        ("O_LftB_LiftDown",  "Y4"),
        ("O_LftB_BeltFwd",   "Y7"),
        ("O_LftB_BeltRev",   "Y6"),
        ("I_LftB_BeltStart", "X11"),
        ("I_LftB_BeltEnd",   "X12"),
        ("I_LftB_UpLimit",   "X7"),
        ("I_LftB_DnLimit",   "X10"),
        ("I_LftB_EmergencyStop", "X14"),
    ],

    "Conveyor": [
        ("O_Conv_BeltFwd",  "Y10"),
        ("I_Conv_BeltEnd",  "X2"),
        ("I_Placon_Full",   "X4"),
    ],

    "Panel": [
        ("I_Pnl_EStop",        "X15"),
        ("I_Pnl_MachineStartPB", "X16"),
        ("I_Pnl_SeqStartPB",   "X13"),
        ("O_Pnl_MachineReady", "Y11"),
        ("O_Pnl_Alarm",        "Y16"),
    ],
}


def dev_index(device):
    """Convert a Mitsubishi device address like 'X15' or 'Y2' to a
    0-based bit index matching INPUT_START/OUTPUT_START ordering."""

    digits = device[1:]

    if len(digits) == 1:
        block, bit = 0, int(digits, 16)
    else:
        block, bit = int(digits[0], 16), int(digits[1], 16)

    return block * 8 + bit


class FX5UHMI:

    def __init__(self, root):

        self.root = root
        self.root.title("FX5U-64MT/ES HMI")
        self.root.geometry("900x650")

        self.client = None
        self.connected = False

        self.input_labels = []
        self.output_buttons = []
        self.output_states = [False] * OUTPUT_COUNT

        # index -> list of (widget, display_name) to keep in sync
        self.input_widget_groups = {i: [] for i in range(INPUT_COUNT)}
        self.output_widget_groups = {i: [] for i in range(OUTPUT_COUNT)}

        self.build_ui()

        self.connect()
        self.poll()

    # ==========================================
    # Mitsubishi Address Naming
    # ==========================================

    def io_name(self, prefix, index):

        block = index // 8
        bit = index % 8

        return f"{prefix}{block:X}{bit:X}"

    # ==========================================
    # UI
    # ==========================================

    def build_ui(self):

        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=5, pady=5)

        self.status_label = tk.Label(
            top,
            text="DISCONNECTED",
            bg="red",
            fg="white",
            width=20,
            font=("Arial", 12, "bold")
        )

        self.status_label.pack(side="left")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        # ======================================
        # INPUTS
        # ======================================

        input_frame = ttk.Frame(notebook)
        notebook.add(input_frame, text="Inputs")

        for i in range(INPUT_COUNT):

            lbl = tk.Label(
                input_frame,
                text=f"{self.io_name('X', i)}\nOFF",
                width=8,
                height=2,
                bg="red",
                fg="white",
                font=("Arial", 10, "bold")
            )

            lbl.grid(
                row=i // 8,
                column=i % 8,
                padx=3,
                pady=3
            )

            self.input_labels.append(lbl)
            self.input_widget_groups[i].append((lbl, self.io_name("X", i)))

        # ======================================
        # OUTPUTS
        # ======================================

        output_frame = ttk.Frame(notebook)
        notebook.add(output_frame, text="Outputs")

        for i in range(OUTPUT_COUNT):

            cell = ttk.Frame(output_frame)

            cell.grid(
                row=i // 8,
                column=i % 8,
                padx=3,
                pady=3
            )

            toggle_btn = tk.Button(
                cell,
                text=f"{self.io_name('Y', i)}\nOFF",
                width=8,
                height=2,
                bg="red",
                fg="white",
                font=("Arial", 10, "bold"),
                command=lambda idx=i: self.toggle_output(idx)
            )

            toggle_btn.pack()

            self.output_buttons.append(toggle_btn)
            self.output_widget_groups[i].append((toggle_btn, self.io_name("Y", i)))

            hold_btn = tk.Button(
                cell,
                text="HOLD",
                width=8,
                height=1,
                bg="gray",
                fg="white",
                font=("Arial", 9, "bold")
            )

            hold_btn.bind("<ButtonPress-1>", lambda e, idx=i: self.momentary_press(idx))
            hold_btn.bind("<ButtonRelease-1>", lambda e, idx=i: self.momentary_release(idx))

            hold_btn.pack()

        # ======================================
        # PERIPHERALS
        # ======================================

        self.build_peripherals_tab(notebook)

    def build_peripherals_tab(self, notebook):

        periph_frame = ttk.Frame(notebook)
        notebook.add(periph_frame, text="Peripherals")

        sections = ["Lifter A", "Lifter B", "Conveyor", "Panel"]

        for s_idx, section in enumerate(sections):

            box = ttk.Labelframe(periph_frame, text=section, padding=8)

            box.grid(
                row=s_idx // 2,
                column=s_idx % 2,
                padx=8,
                pady=8,
                sticky="nsew"
            )

            for r, (label_name, device) in enumerate(PERIPHERALS[section]):

                short_name = label_name.split("_", 2)[-1]
                index = dev_index(device)
                display = f"{short_name} ({device})"

                if device[0] == "X":

                    lbl = tk.Label(
                        box,
                        text=f"{display}\nOFF",
                        width=22,
                        height=2,
                        bg="red",
                        fg="white",
                        font=("Arial", 10, "bold"),
                        justify="center"
                    )

                    lbl.grid(row=r, column=0, padx=4, pady=3, sticky="w")

                    self.input_widget_groups[index].append((lbl, display))

                else:

                    cell = ttk.Frame(box)
                    cell.grid(row=r, column=0, padx=4, pady=3, sticky="w")

                    toggle_btn = tk.Button(
                        cell,
                        text=f"{display}\nOFF",
                        width=22,
                        height=2,
                        bg="red",
                        fg="white",
                        font=("Arial", 10, "bold"),
                        command=lambda idx=index: self.toggle_output(idx)
                    )

                    toggle_btn.pack(side="left")

                    self.output_widget_groups[index].append((toggle_btn, display))

                    hold_btn = tk.Button(
                        cell,
                        text="HOLD",
                        width=8,
                        height=2,
                        bg="gray",
                        fg="white",
                        font=("Arial", 9, "bold")
                    )

                    hold_btn.bind("<ButtonPress-1>", lambda e, idx=index: self.momentary_press(idx))
                    hold_btn.bind("<ButtonRelease-1>", lambda e, idx=index: self.momentary_release(idx))

                    hold_btn.pack(side="left", padx=(4, 0))

        for c in range(2):
            periph_frame.columnconfigure(c, weight=1)

    # ==========================================
    # CONNECTION
    # ==========================================

    def connect(self):

        try:

            if self.client:
                self.client.close()

            self.client = ModbusTcpClient(
                host=PLC_IP,
                port=PLC_PORT
            )

            self.connected = self.client.connect()

            if self.connected:

                self.status_label.config(
                    text="CONNECTED",
                    bg="green"
                )

            else:

                self.status_label.config(
                    text="DISCONNECTED",
                    bg="red"
                )

        except Exception as e:

            print("Connect Error:", e)

            self.connected = False

            self.status_label.config(
                text="DISCONNECTED",
                bg="red"
            )

    # ==========================================
    # LED DISPLAY
    # ==========================================

    def set_led(self, widget, name, state):

        widget.config(
            text=f"{name}\n{'ON' if state else 'OFF'}",
            bg="green" if state else "red"
        )

    def update_input_led(self, index, state):

        for widget, name in self.input_widget_groups[index]:
            self.set_led(widget, name, state)

    def update_output_led(self, index, state):

        for widget, name in self.output_widget_groups[index]:
            self.set_led(widget, name, state)

    # ==========================================
    # WRITE OUTPUT
    # ==========================================

    def write_output(self, index, state):

        if not self.connected:
            return

        try:

            result = self.client.write_coil(
                OUTPUT_START + index,
                state,
                device_id=DEVICE_ID
            )

            if not result.isError():

                self.output_states[index] = state

                self.update_output_led(index, state)

        except Exception as e:

            print("Write Error:", e)

            self.connected = False

    def toggle_output(self, index):

        self.write_output(index, not self.output_states[index])

    def momentary_press(self, index):

        self.write_output(index, True)

    def momentary_release(self, index):

        self.write_output(index, False)

    # ==========================================
    # POLL PLC
    # ==========================================

    def poll(self):

        try:

            if not self.connected:
                self.connect()

            if self.connected:

                # -----------------------------
                # READ INPUTS X0-X37
                # -----------------------------

                rr = self.client.read_discrete_inputs(
                    INPUT_START,
                    count=INPUT_COUNT,
                    device_id=DEVICE_ID
                )

                if not rr.isError():

                    for i in range(min(INPUT_COUNT, len(rr.bits))):

                        self.update_input_led(i, rr.bits[i])

                # -----------------------------
                # READ OUTPUTS Y0-Y37
                # -----------------------------

                rr = self.client.read_coils(
                    OUTPUT_START,
                    count=OUTPUT_COUNT,
                    device_id=DEVICE_ID
                )

                if not rr.isError():

                    for i in range(min(OUTPUT_COUNT, len(rr.bits))):

                        self.output_states[i] = rr.bits[i]

                        self.update_output_led(i, rr.bits[i])

        except Exception as e:

            print("Poll Error:", e)

            self.connected = False

            self.status_label.config(
                text="DISCONNECTED",
                bg="red"
            )

        self.root.after(POLL_MS, self.poll)


def main():

    root = tk.Tk()

    app = FX5UHMI(root)

    root.mainloop()


if __name__ == "__main__":
    main()