import os
from pathlib import Path


SAMPLE_QUOTES = {
    "RKSE-004-058": {
        "material": "AL6061-T6",
        "qty": 1,
        "rm_cost": 59,
        "processing": 1956,
        "oh_others": 0,
        "extra_label": "Plating",
        "extra_cost": 600,
        "internal_total": 2615,
        "quoted_price": 2789,
        "notes": "Demo sample from master_cost_sheet.xlsx",
    },
    "RKSE-004-059": {
        "material": "AL6061-T6",
        "qty": 1,
        "rm_cost": 59,
        "processing": 1956,
        "oh_others": 0,
        "extra_label": "Plating",
        "extra_cost": 600,
        "internal_total": 2615,
        "quoted_price": 2789,
        "notes": "Demo sample from master_cost_sheet.xlsx",
    },
    "RKSE-004-060": {
        "material": "AL6061-T6",
        "qty": 1,
        "rm_cost": 59,
        "processing": 2103,
        "oh_others": 0,
        "extra_label": "Plating",
        "extra_cost": 600,
        "internal_total": 2762,
        "quoted_price": 3939,
        "notes": "Demo sample from master_cost_sheet.xlsx",
    },
}


def normalize_part_name(step_name: str) -> str:
    base = os.path.basename(step_name.strip())
    stem = Path(base).stem
    return stem.upper()


def ask(prompt: str, default: str = "") -> str:
    value = input(f"{prompt}: ").strip()
    return value or default


def print_header() -> None:
    print("=" * 66)
    print("INSTA QUOTE DEMO")
    print("Terminal mock flow for CAD -> quote experience")
    print("=" * 66)
    print("Upload STEP, enter commercial inputs, get quote instantly.")
    print()


def print_quote(part_number: str, step_file: str, qty: int, material: str, finish: str,
                inspection: str, shipping: str, packaging: str) -> None:
    sample = SAMPLE_QUOTES[part_number]

    rm_cost = sample["rm_cost"]
    processing = sample["processing"]
    oh_others = sample["oh_others"]
    extra_cost = sample["extra_cost"]
    internal_total = sample["internal_total"]
    quoted_price = sample["quoted_price"]

    print()
    print("-" * 66)
    print("Input Summary")
    print("-" * 66)
    print(f"STEP file        : {step_file}")
    print(f"Part number      : {part_number}")
    print(f"Quantity         : {qty}")
    print(f"Material         : {material}")
    print(f"Surface finish   : {finish}")
    print(f"Inspection       : {inspection}")
    print(f"Shipping         : {shipping}")
    print(f"Packaging        : {packaging}")

    print()
    print("-" * 66)
    print("Quote Breakdown")
    print("-" * 66)
    print(f"Raw material               INR {rm_cost}")
    print(f"Processing / machining     INR {processing}")
    print(f"OH & others                INR {oh_others}")
    print(f"{sample['extra_label']:<26} INR {extra_cost}")
    print(f"{'Internal total / pc':<26} INR {internal_total}")
    print(f"{'Quoted price / pc':<26} INR {quoted_price}")

    print()
    print("-" * 66)
    print("Demo Narrative")
    print("-" * 66)
    print("STEP uploaded successfully.")
    print("Geometry features recognized.")
    print("Manufacturing route estimated.")
    print("Commercial rules applied.")
    print("Instant quote generated.")
    print()
    print(f"Final answer: Quote for {part_number} is INR {quoted_price} per piece.")
    print(f"Reference source: {sample['notes']}")
    print("=" * 66)


def main() -> None:
    print_header()

    step_file = ask("Enter STEP file name", "RKSE-004-058.stp")
    part_number = normalize_part_name(step_file)

    if part_number not in SAMPLE_QUOTES:
        print()
        print("This demo build currently supports only the prepared sample STEP files.")
        return

    sample = SAMPLE_QUOTES[part_number]

    qty_raw = ask("Enter quantity", str(sample["qty"]))
    try:
        qty = int(qty_raw)
    except ValueError:
        qty = sample["qty"]

    material = ask("Enter material", sample["material"])
    finish = ask("Enter surface finish", "Plating")
    inspection = ask("Enter inspection type", "Standard")
    shipping = ask("Enter shipping mode", "Standard")
    packaging = ask("Enter packaging type", "Standard")

    print()
    print("Running quote engine demo...")
    print("Loading CAD...")
    print("Extracting features...")
    print("Estimating manufacturing cost...")
    print("Applying pricing rules...")

    print_quote(
        part_number=part_number,
        step_file=step_file,
        qty=qty,
        material=material,
        finish=finish,
        inspection=inspection,
        shipping=shipping,
        packaging=packaging,
    )


if __name__ == "__main__":
    main()
