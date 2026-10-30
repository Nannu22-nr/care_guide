import io
import json
import random
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent / "dataset"
OUT.mkdir(exist_ok=True)
random.seed(42)

SUPPLIERS = ["Acme Corp", "Northwind Traders", "Globex Ltd", "Initech LLC", "Umbrella Supplies",
             "Stark Industries", "Wayne Logistics", "Hooli Systems", "Dhaka Fresh Foods", "Padma Textiles"]
CUSTOMERS = ["Beta Ltd", "Gamma Inc", "Delta Retail", "Sundarban Mart", "Meghna Traders", "Blue Ocean Co"]
ITEMS = ["Widget A", "Widget B", "Steel bolts (box)", "Printer paper A4", "USB-C cable",
         "Consulting hour", "Packaging service", "Cotton fabric (m)", "LED panel", "Maintenance fee"]
CURRENCIES = [("USD", "$"), ("EUR", "EUR "), ("GBP", "GBP "), ("BDT", "BDT ")]
DATE_STYLES = [("%Y-%m-%d", "iso"), ("%d/%m/%Y", "dmy"), ("%B %d, %Y", "long")]


def money(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def make_invoice(i, *, tax_rate=Decimal("0.10"), hide_tax=False, hide_customer=False):
    supplier = SUPPLIERS[i % len(SUPPLIERS)]
    customer = None if hide_customer else random.choice(CUSTOMERS)
    cur, sym = random.choice(CURRENCIES)
    n_items = random.randint(1, 5)
    items = []
    for _ in range(n_items):
        qty = random.randint(1, 9)
        price = money(random.randint(500, 50000) / 100)
        items.append({"description": random.choice(ITEMS), "quantity": qty,
                      "unit_price": float(price), "amount": float(money(qty * price))})
    subtotal = money(sum(Decimal(str(it["amount"])) for it in items))
    tax = Decimal("0.00") if hide_tax else money(subtotal * tax_rate)
    total = subtotal + tax
    from datetime import date, timedelta
    d = date(2024, 1, 1) + timedelta(days=random.randint(0, 600))
    fmt, _ = random.choice(DATE_STYLES)
    return {
        "invoice_number": f"INV-{2000 + i}",
        "invoice_date_iso": d.isoformat(),
        "invoice_date_text": d.strftime(fmt),
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": float(subtotal),
        "tax": None if hide_tax else float(tax),  
        "total": float(total),
        "currency": cur, "sym": sym,
        "line_items": items,
        "tax_rate": float(tax_rate),
    }


def to_truth(inv):
    return {
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date_iso"],
        "supplier_name": inv["supplier_name"],
        "customer_name": inv["customer_name"],
        "subtotal": inv["subtotal"],
        "tax": inv["tax"],
        "total": inv["total"],
        "currency": inv["currency"],
        "line_items": inv["line_items"],
    }


def lines_for(inv):
    s = inv["sym"]
    L = [inv["supplier_name"].upper(), "INVOICE", "",
         f"Invoice No: {inv['invoice_number']}",
         f"Date: {inv['invoice_date_text']}"]
    if inv["customer_name"]:
        L.append(f"Bill To: {inv['customer_name']}")
    L += ["", "Description        Qty   Unit Price    Amount"]
    for it in inv["line_items"]:
        L.append(f"{it['description'][:18]:<18} {it['quantity']:>3}   {s}{it['unit_price']:>9.2f}   {s}{it['amount']:>9.2f}")
    L += ["", f"Subtotal: {s}{inv['subtotal']:.2f}"]
    if inv["tax"] is not None:
        L.append(f"Tax ({inv['tax_rate']:.0%}): {s}{inv['tax']:.2f}")
    L.append(f"TOTAL: {s}{inv['total']:.2f}")
    L.append(f"Currency: {inv['currency']}")
    return L


def write_pdf(inv, path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in lines_for(inv):
        page.insert_text((50, y), line, fontsize=11, fontname="cour")
        y += 18
    doc.save(path)
    doc.close()


def render_png(inv, *, noisy=False):
    lines = lines_for(inv)
    img = Image.new("RGB", (1000, 60 + 34 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    for k, line in enumerate(lines):
        d.text((40, 30 + 34 * k), line, fill="black", font=font)
    if noisy:                                   # poor-quality "scan"
        img = img.rotate(random.uniform(-2.5, 2.5), expand=True, fillcolor="white")
        img = img.filter(ImageFilter.GaussianBlur(0.8))
        px = img.load()
        for _ in range(img.width * img.height // 80):
            x, y = random.randrange(img.width), random.randrange(img.height)
            v = random.randint(120, 255)
            px[x, y] = (v, v, v)
    return img


def write_xlsx(inv, path, multi_sheet=False):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["Invoice No", inv["invoice_number"]])
    ws.append(["Date", inv["invoice_date_iso"]])
    ws.append(["Supplier", inv["supplier_name"]])
    if inv["customer_name"]:
        ws.append(["Customer", inv["customer_name"]])
    ws.append(["Currency", inv["currency"]])
    ws.append([])
    ws.append(["Description", "Qty", "Unit Price", "Amount"])
    for it in inv["line_items"]:
        ws.append([it["description"], it["quantity"], it["unit_price"], it["amount"]])
    ws.append([])
    ws.append(["Subtotal", inv["subtotal"]])
    if inv["tax"] is not None:
        ws.append(["Tax", inv["tax"]])
    ws.append(["Total", inv["total"]])
    if multi_sheet:
        n = wb.create_sheet("Notes")
        n.append(["Payment terms", "Net 30"])
        n.append(["Remarks", "Thank you for your business"])
    wb.save(path)


def write_csv(inv, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["invoice_number", "invoice_date", "supplier", "customer", "currency",
                    "description", "quantity", "unit_price", "amount", "subtotal", "tax", "total"])
        for it in inv["line_items"]:
            w.writerow([inv["invoice_number"], inv["invoice_date_iso"], inv["supplier_name"],
                        inv["customer_name"] or "", inv["currency"], it["description"],
                        it["quantity"], it["unit_price"], it["amount"],
                        inv["subtotal"], "" if inv["tax"] is None else inv["tax"], inv["total"]])


def main():
    truth = {}
    n = 0

    def add(name, inv):
        truth[name] = to_truth(inv)

    # 8 digital PDFs
    for _ in range(8):
        inv = make_invoice(n); n += 1
        name = f"pdf_{inv['invoice_number']}.pdf"
        write_pdf(inv, OUT / name); add(name, inv)
    # 2 PDFs: no tax printed / no customer (truth must be null)
    for kw in ({"hide_tax": True}, {"hide_customer": True}):
        inv = make_invoice(n, **kw); n += 1
        name = f"pdf_hard_{inv['invoice_number']}.pdf"
        write_pdf(inv, OUT / name); add(name, inv)
    # 4 clean PNG + 4 noisy "scanned" PNG
    for noisy in (False,) * 4 + (True,) * 4:
        inv = make_invoice(n); n += 1
        name = f"{'scan' if noisy else 'img'}_{inv['invoice_number']}.png"
        render_png(inv, noisy=noisy).save(OUT / name); add(name, inv)
    # 2 scanned PDFs (image-only pages -> OCR route)
    import fitz
    for _ in range(2):
        inv = make_invoice(n); n += 1
        buf = io.BytesIO(); render_png(inv, noisy=True).save(buf, format="PNG")
        name = f"scanpdf_{inv['invoice_number']}.pdf"
        doc = fitz.open(); page = doc.new_page(width=700, height=900)
        page.insert_image(page.rect, stream=buf.getvalue()); doc.save(OUT / name); doc.close()
        add(name, inv)
    # 3 XLSX (one multi-sheet) + 3 CSV
    for k in range(3):
        inv = make_invoice(n); n += 1
        name = f"xlsx_{inv['invoice_number']}.xlsx"
        write_xlsx(inv, OUT / name, multi_sheet=(k == 0)); add(name, inv)
    for _ in range(3):
        inv = make_invoice(n); n += 1
        name = f"csv_{inv['invoice_number']}.csv"
        write_csv(inv, OUT / name); add(name, inv)

    (OUT / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    by_type = {}
    for k in truth:
        by_type[k.rsplit(".", 1)[-1]] = by_type.get(k.rsplit(".", 1)[-1], 0) + 1
    print(f"Wrote {len(truth)} labelled documents to {OUT}  {by_type}")


if __name__ == "__main__":
    main()
