"""One-time script: renders the (fictional) NorthPeak Outdoor Gear customer
service policy into data/policy.pdf.

The policy TEXT is company-specific — that is fine, it lives under data/ as an
input. The rest of the system never hardcodes any of these rules; it reads
whatever PDF sits at data/policy.pdf. Each rule deliberately states BOTH the
grant condition and the explicit denial condition so a compliance judge can
determine right vs. wrong from the document alone.
"""

from pathlib import Path

from fpdf import FPDF

POLICY_TITLE = "NorthPeak Outdoor Gear - Customer Service Policy (v3.1, effective 2026-01-01)"

RULES = [
    ("R1.1 Returns - standard window",
     "A return requested within 30 days of the delivery date, where the item is unworn and "
     "in original condition, MUST be granted a full refund to the original payment method. "
     "A return of a worn or damaged-by-customer item does NOT qualify for a refund under this rule."),
    ("R1.2 Returns - extended window",
     "A return requested between 31 and 90 days after the delivery date MUST be offered store "
     "credit only. A full cash refund MUST NOT be offered in this window, even if the item is unworn."),
    ("R1.3 Returns - final sale",
     "Items marked final sale are NOT eligible for return, refund, or store credit under any "
     "circumstances. Agents MUST decline final-sale return requests; no exceptions may be granted."),
    ("R1.4 Returns - expired",
     "A return requested more than 90 days after the delivery date MUST be declined. Neither "
     "refund nor store credit may be offered."),
    ("R2.1 Shipping - delay",
     "If an order is delivered more than 5 business days after the promised delivery date, the "
     "customer MUST be offered a shipping credit equal to the shipping fee paid (minimum $10 "
     "credit). Delays of 5 business days or fewer do NOT qualify for compensation."),
    ("R2.2 Shipping - lost package",
     "If a package is confirmed lost by the carrier, the customer MUST be offered a choice "
     "between a free replacement and a full refund. If the carrier has not confirmed the loss, "
     "the agent MUST first open a carrier trace and MUST NOT yet promise a refund or replacement."),
    ("R2.3 Shipping - damaged in transit",
     "If an item arrives damaged in transit, the customer MUST be sent a free replacement and "
     "does NOT need to return the damaged item. A refund instead of a replacement is NOT the "
     "default remedy under this rule and requires the customer to explicitly decline a replacement."),
    ("R3.1 Cancellation - before shipment",
     "An order cancelled before it has shipped MUST receive a full refund with no cancellation "
     "fee. Agents MUST NOT charge restocking or processing fees for pre-shipment cancellations."),
    ("R3.2 Cancellation - after shipment",
     "An order cannot be cancelled once shipped. The request MUST be handled as a return under "
     "rules R1.1-R1.4 after delivery. Agents MUST NOT intercept or recall shipped packages."),
    ("R4.1 Warranty - manufacturing defect",
     "A verified manufacturing defect reported within 1 year of the delivery date MUST be "
     "remedied with a free replacement of the same or equivalent product. Defects reported "
     "after 1 year are NOT covered."),
    ("R4.2 Warranty - misuse",
     "Damage resulting from misuse, accident, or normal wear and tear is NOT covered by "
     "warranty. Such claims MUST be declined, though the agent MAY offer a one-time 15% "
     "discount code on a replacement purchase as goodwill."),
    ("R5.1 Billing - duplicate charge",
     "A confirmed duplicate charge MUST be refunded immediately in full. If the duplicate is "
     "not yet confirmed in the billing system, the agent MUST open a billing investigation and "
     "reply within 2 business days; the agent MUST NOT deny the claim outright."),
    ("R5.2 Billing - price match",
     "A price-match request MUST be honored only when made within 7 days of purchase AND the "
     "competitor is a listed authorized retailer. Requests outside 7 days, or citing "
     "non-authorized sellers or marketplace listings, MUST be declined."),
    ("R6 Escalation - high value or unclear",
     "Any case where the disputed value exceeds $200, or that does not clearly fall under rules "
     "R1-R5, MUST be escalated to a human senior agent. The system or agent MUST NOT resolve "
     "such cases autonomously, even if a lower-numbered rule appears to apply."),
    ("R7 Escalation - frequent returner",
     "Any customer with 3 or more returns in the past 90 days MUST be flagged for manual "
     "review before any new return or refund is approved, even if the individual request would "
     "otherwise qualify under R1. The agent MUST NOT auto-approve the remedy; the reply should "
     "acknowledge the request and state it is under review."),
]


def build(out_path: str = "data/policy.pdf") -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, POLICY_TITLE, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for heading, body in RULES:
        pdf.set_font("Helvetica", "B", 11)
        pdf.multi_cell(0, 6, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, body, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    pdf.output(out_path)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    build()
