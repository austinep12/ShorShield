"""
Topic 23: Shor's 9-Qubit Code
=============================
Encodes 1 logical qubit into 9 physical qubits, injects a single-qubit
Pauli error (X, Y or Z) on any qubit, extracts the error syndrome with
8 ancilla qubits, applies the correction chosen by that syndrome, decodes,
and checks that the original state was restored.

Outputs (written next to this script):
  results_table.csv        - Error Location vs. Syndrome vs. Recovery table
  circuit_full.png         - full circuit (encode, error, syndrome, correct, decode)
  circuit_encoder.png      - encoder only (easier to read on a slide)

Run:  python shor_9qubit_code.py
"""
import os

# Import Aer before matplotlib: on some Windows setups loading Aer's native
# library after matplotlib.pyplot crashes the interpreter (access violation).
from qiskit_aer import AerSimulator
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
import matplotlib
matplotlib.use("Agg")  # save figures to file; works without a display
import matplotlib.pyplot as plt
import pandas as pd

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SHOTS = 256

# Data-qubit blocks used by the bit-flip (inner) layer of the code
BLOCKS = [(0, 1, 2), (3, 4, 5), (6, 7, 8)]

# Input states to protect: (label, preparation on qubit 0, expected outcome).
# Each state is un-prepared after decoding, so a perfect recovery
# always reads '0'. |+> catches phase errors that |0>/|1> cannot see.
INPUT_STATES = {
    "|0>": lambda qc, q: None,
    "|1>": lambda qc, q: qc.x(q),
    "|+>": lambda qc, q: qc.h(q),
    "arbitrary": lambda qc, q: (qc.ry(1.1, q), qc.rz(0.7, q)),
}
UNPREPARE = {
    "|0>": lambda qc, q: None,
    "|1>": lambda qc, q: qc.x(q),
    "|+>": lambda qc, q: qc.h(q),
    "arbitrary": lambda qc, q: (qc.rz(-0.7, q), qc.ry(-1.1, q)),
}


def encode(qc, d):
    """|psi> -> Shor logical state on data qubits d[0..8]."""
    # Outer layer: phase-flip code across the three block leaders
    qc.cx(d[0], d[3])
    qc.cx(d[0], d[6])
    for b in (0, 3, 6):
        qc.h(d[b])
    # Inner layer: bit-flip code inside each block
    for b0, b1, b2 in BLOCKS:
        qc.cx(d[b0], d[b1])
        qc.cx(d[b0], d[b2])


def decode(qc, d):
    """Exact inverse of encode()."""
    for b0, b1, b2 in BLOCKS:
        qc.cx(d[b0], d[b2])
        qc.cx(d[b0], d[b1])
    for b in (0, 3, 6):
        qc.h(d[b])
    qc.cx(d[0], d[6])
    qc.cx(d[0], d[3])


def inject_error(qc, d, error_type, error_qubit):
    if error_type == "X":
        qc.x(d[error_qubit])
    elif error_type == "Y":
        qc.y(d[error_qubit])
    elif error_type == "Z":
        qc.z(d[error_qubit])


def extract_and_correct(qc, d, anc, syn_bit, syn_phase):
    """
    Bit-flip syndrome: for each block measure Z_a Z_b and Z_b Z_c (6 ancillas).
    Phase-flip syndrome: measure X^6 on blocks 1+2 and on blocks 2+3 (2 ancillas).
    Then apply the correction picked by each 2-bit syndrome.
    """
    # --- Bit-flip syndrome (parity checks inside each block) ---
    for k, (b0, b1, b2) in enumerate(BLOCKS):
        a1, a2 = anc[2 * k], anc[2 * k + 1]
        qc.cx(d[b0], a1); qc.cx(d[b1], a1)   # Z_b0 Z_b1
        qc.cx(d[b1], a2); qc.cx(d[b2], a2)   # Z_b1 Z_b2
        qc.measure(a1, syn_bit[k][0])
        qc.measure(a2, syn_bit[k][1])

    # --- Phase-flip syndrome (X-parity between neighbouring blocks) ---
    p1, p2 = anc[6], anc[7]
    qc.h(p1); qc.h(p2)
    for q in range(0, 6):
        qc.cx(p1, d[q])                      # X0..X5
    for q in range(3, 9):
        qc.cx(p2, d[q])                      # X3..X8
    qc.h(p1); qc.h(p2)
    qc.measure(p1, syn_phase[0])
    qc.measure(p2, syn_phase[1])
    qc.barrier()

    # --- Correction (register value = bit0 + 2*bit1) ---
    # Bit flips: (s1,s2)=(1,0)->first qubit, (1,1)->middle, (0,1)->last
    for k, (b0, b1, b2) in enumerate(BLOCKS):
        for value, target in ((1, b0), (3, b1), (2, b2)):
            with qc.if_test((syn_bit[k], value)):
                qc.x(d[target])
    # Phase flips: a Z on any qubit of the faulty block fixes its sign
    for value, target in ((1, 0), (3, 3), (2, 6)):
        with qc.if_test((syn_phase, value)):
            qc.z(d[target])


def build_shor_circuit(error_type="None", error_qubit=None, state="|0>"):
    d = QuantumRegister(9, "q")
    anc = QuantumRegister(8, "anc")
    syn_bit = [ClassicalRegister(2, f"sb{k + 1}") for k in range(3)]
    syn_phase = ClassicalRegister(2, "sp")
    out = ClassicalRegister(1, "out")
    qc = QuantumCircuit(d, anc, *syn_bit, syn_phase, out)

    INPUT_STATES[state](qc, d[0])
    encode(qc, d)
    qc.barrier()
    inject_error(qc, d, error_type, error_qubit)
    qc.barrier()
    extract_and_correct(qc, d, anc, syn_bit, syn_phase)
    qc.barrier()
    decode(qc, d)
    UNPREPARE[state](qc, d[0])
    qc.measure(d[0], out[0])
    return qc


def parse_syndrome(key):
    """
    Counts key looks like 'out sp sb3 sb2 sb1' with each register MSB-first.
    Return readable strings in check order (first check first).
    """
    out, sp, sb3, sb2, sb1 = key.split()
    bit = " ".join(r[::-1] for r in (sb1, sb2, sb3))
    phase = sp[::-1]
    return bit, phase, out


def diagnose(bit_syn, phase_syn):
    """Translate a syndrome into the correction the circuit applies."""
    lookup = {"10": 0, "11": 1, "01": 2}
    fixes = []
    for k, s in enumerate(bit_syn.split()):
        if s in lookup:
            fixes.append(f"X on q{BLOCKS[k][lookup[s]]}")
    if phase_syn in lookup:
        fixes.append(f"Z on block {lookup[phase_syn] + 1}")
    return ", ".join(fixes) if fixes else "none"


def main():
    # Matrix-product-state handles the 17-qubit circuit with mid-circuit
    # measurements ~20x faster than statevector; single-threaded for stability.
    sim = AerSimulator(method="matrix_product_state", max_parallel_threads=1)
    cases = [("None", None)] + [(e, q) for e in ("X", "Y", "Z") for q in range(9)]
    rows = []

    print("Running Shor's 9-qubit code verification "
          f"({len(cases)} error cases x {len(INPUT_STATES)} input states, {SHOTS} shots each)...\n")

    for err_type, err_q in cases:
        row = {
            "Error Type": err_type,
            "Error Qubit": "-" if err_q is None else f"q{err_q}",
            "Error Block": "-" if err_q is None else err_q // 3 + 1,
        }
        for state in INPUT_STATES:
            qc = transpile(build_shor_circuit(err_type, err_q, state), sim)
            counts = sim.run(qc, shots=SHOTS).result().get_counts()
            ok = sum(n for k, n in counts.items() if parse_syndrome(k)[2] == "0")
            row[f"Recovered {state}"] = f"{100 * ok / SHOTS:.0f}%"
            if state == "|0>":
                # Syndrome is deterministic for a single Pauli error on a code state;
                # take it from the |0> run. (Under a Z error the phase checks fire
                # identically for every input state.)
                bit_syn, phase_syn, _ = parse_syndrome(max(counts, key=counts.get))
                row["Bit-flip Syndrome (B1 B2 B3)"] = bit_syn
                row["Phase Syndrome"] = phase_syn
                row["Correction Applied"] = diagnose(bit_syn, phase_syn)
        rows.append(row)

    cols = ["Error Type", "Error Qubit", "Error Block",
            "Bit-flip Syndrome (B1 B2 B3)", "Phase Syndrome", "Correction Applied"] + \
           [f"Recovered {s}" for s in INPUT_STATES]
    df = pd.DataFrame(rows)[cols]

    print("=== SHOR'S 9-QUBIT CODE: ERROR LOCATION vs SYNDROME vs RECOVERY ===")
    print(df.to_string(index=False))
    all_ok = all(df[c].eq("100%").all() for c in cols if c.startswith("Recovered"))
    print(f"\nAll single-qubit errors corrected for every input state: {all_ok}")

    csv_path = os.path.join(OUT_DIR, "results_table.csv")
    df.to_csv(csv_path, index=False)
    print(f"Saved table   -> {csv_path}")

    # --- Circuit diagrams ---
    full = build_shor_circuit("Y", 4, "|0>")
    fig = full.draw("mpl", fold=-1)
    fig.suptitle("Shor's 9-Qubit Code: encode | Y error on q4 | syndrome + correct | decode",
                 fontsize=14)
    full_path = os.path.join(OUT_DIR, "circuit_full.png")
    fig.savefig(full_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    enc = QuantumCircuit(9, name="encoder")
    encode(enc, enc.qubits)
    fig = enc.draw("mpl")
    fig.suptitle("Shor's 9-Qubit Encoder", fontsize=14)
    enc_path = os.path.join(OUT_DIR, "circuit_encoder.png")
    fig.savefig(enc_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved diagram -> {full_path}")
    print(f"Saved diagram -> {enc_path}")


if __name__ == "__main__":
    main()
